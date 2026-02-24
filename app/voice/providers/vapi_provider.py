"""
VAPI telephony provider.

VAPI is an AI-voice platform that can also serve as a raw telephony
backend. We use its "server URL" mode: VAPI places / receives calls
and forwards speech to our webhook, where we run Gemini ourselves.

Call control responses use VAPI's JSON format instead of XML.

Docs: https://docs.vapi.ai
"""

from __future__ import annotations

import json
import logging

import httpx

from app.voice.providers.base import CallResult, VoiceProvider, WebhookData

logger = logging.getLogger(__name__)

VAPI_API_BASE = "https://api.vapi.ai"


class VAPIProvider(VoiceProvider):

    def __init__(self, settings):
        self._settings = settings

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._settings.vapi_api_key}",
            "Content-Type": "application/json",
        }

    # ── Identity ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "VAPI"

    @property
    def phone_number(self) -> str:
        return self._settings.vapi_phone_number

    def is_configured(self) -> bool:
        return bool(
            self._settings.vapi_api_key
            and self._settings.vapi_phone_number
        )

    # ── Outbound ──────────────────────────────────────────────

    def initiate_call(self, to: str, answer_url: str, status_url: str) -> CallResult:
        """Create an outbound call via the VAPI REST API."""
        resp = httpx.post(
            f"{VAPI_API_BASE}/call/phone",
            headers=self._headers(),
            json={
                "phoneNumberId": self._settings.vapi_phone_number_id,
                "customer": {"number": to},
                "serverUrl": answer_url,
                "serverUrlSecret": self._settings.vapi_server_secret,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return CallResult(
            call_sid=data.get("id", ""),
            status=data.get("status", "queued"),
        )

    # ── Call control (JSON responses) ─────────────────────────
    #
    # VAPI's server-URL mode expects JSON responses that tell
    # VAPI what to say and whether to keep listening.
    # We wrap them in the same XML envelope so the router's
    # content-type handling stays uniform, but VAPI actually
    # parses the inner JSON from our /voice/* endpoints.
    #
    # In practice VAPI uses "assistant-request" / "function-call"
    # webhooks.  For our integration we return "say" instructions.

    def build_gather(
        self,
        message: str,
        action_url: str,
        timeout_url: str,
        timeout_message: str,
    ) -> str:
        return json.dumps({
            "messageResponse": {
                "type": "assistant-message",
                "message": message,
                "endCallAfterSpoken": False,
            }
        })

    def build_say_hangup(self, *messages: str) -> str:
        combined = " ".join(messages)
        return json.dumps({
            "messageResponse": {
                "type": "assistant-message",
                "message": combined,
                "endCallAfterSpoken": True,
            }
        })

    def build_say_dial(self, message: str, dial_number: str) -> str:
        return json.dumps({
            "messageResponse": {
                "type": "assistant-message",
                "message": message,
                "endCallAfterSpoken": False,
                "transferTo": dial_number,
            }
        })

    @property
    def content_type(self) -> str:
        return "application/json"

    # ── Webhook parsing ───────────────────────────────────────
    #
    # VAPI sends JSON webhooks.  We normalise the key fields
    # so the handler code stays provider-agnostic.

    def parse_webhook(self, form_data: dict) -> WebhookData:
        # VAPI webhooks arrive as JSON with "message" wrapper
        message = form_data.get("message", form_data)
        call = message.get("call", {})
        transcript = message.get("transcript", "")
        # For speech-result events
        if not transcript and "artifact" in message:
            messages = message["artifact"].get("messages", [])
            if messages:
                transcript = messages[-1].get("content", "")

        return WebhookData(
            call_sid=call.get("id", form_data.get("call_id", "")),
            from_number=call.get("customer", {}).get("number", ""),
            to_number=call.get("phoneNumber", {}).get("number", self.phone_number),
            speech_result=transcript,
            confidence="1.0",
            call_status=message.get("type", call.get("status", "")),
        )
