"""
Telnyx telephony provider.

Telnyx supports TeXML — a TwiML-compatible XML dialect — so we can
build the same <Response><Gather><Say> trees.  The main differences:
  - REST API uses telnyx SDK instead of twilio
  - Webhook field names differ slightly (call_control_id, etc.)
  - TeXML responses are served from a TeXML Application in Telnyx portal
"""

from __future__ import annotations

import logging
from xml.etree.ElementTree import Element, SubElement, tostring

import httpx

from app.voice.providers.base import CallResult, VoiceProvider, WebhookData

logger = logging.getLogger(__name__)


def _texml(*children_fn) -> str:
    """Build a TeXML <Response> document."""
    root = Element("Response")
    for fn in children_fn:
        fn(root)
    return '<?xml version="1.0" encoding="UTF-8"?>' + tostring(root, encoding="unicode")


class TelnyxProvider(VoiceProvider):

    def __init__(self, settings):
        self._settings = settings

    # ── Identity ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "Telnyx"

    @property
    def phone_number(self) -> str:
        return self._settings.telnyx_phone_number

    def is_configured(self) -> bool:
        return bool(
            self._settings.telnyx_api_key
            and self._settings.telnyx_phone_number
        )

    # ── Outbound ──────────────────────────────────────────────

    def initiate_call(self, to: str, answer_url: str, status_url: str) -> CallResult:
        """Initiate an outbound call via the Telnyx TeXML REST API."""
        resp = httpx.post(
            "https://api.telnyx.com/v2/texml/calls/{app_id}".format(
                app_id=self._settings.telnyx_app_id,
            ),
            headers={
                "Authorization": f"Bearer {self._settings.telnyx_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "to": to,
                "from": self.phone_number,
                "url": answer_url,
                "status_callback": status_url,
                "status_callback_method": "POST",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        return CallResult(
            call_sid=data.get("call_sid", data.get("sid", "")),
            status=data.get("status", "queued"),
        )

    # ── Call control (TeXML — TwiML-compatible) ───────────────

    def build_gather(
        self,
        message: str,
        action_url: str,
        timeout_url: str,
        timeout_message: str,
    ) -> str:
        s = self._settings

        def _build(root: Element):
            gather = SubElement(root, "Gather", {
                "input": "speech",
                "action": action_url,
                "method": "POST",
                "timeout": str(s.gather_timeout),
                "speechTimeout": str(s.speech_timeout),
                "language": s.language,
            })
            say = SubElement(gather, "Say", {"voice": s.tts_voice})
            say.text = message

        def _timeout(root: Element):
            say = SubElement(root, "Say", {"voice": s.tts_voice})
            say.text = timeout_message
            SubElement(root, "Redirect", {"method": "POST"}).text = timeout_url

        return _texml(_build, _timeout)

    def build_say_hangup(self, *messages: str) -> str:
        s = self._settings

        def _build(root: Element):
            for msg in messages:
                say = SubElement(root, "Say", {"voice": s.tts_voice})
                say.text = msg
            SubElement(root, "Hangup")

        return _texml(_build)

    def build_say_dial(self, message: str, dial_number: str) -> str:
        s = self._settings

        def _build(root: Element):
            say1 = SubElement(root, "Say", {"voice": s.tts_voice})
            say1.text = message
            say2 = SubElement(root, "Say", {"voice": s.tts_voice})
            say2.text = "Let me connect you with a team member. Please hold."
            dial = SubElement(root, "Dial")
            dial.text = dial_number

        return _texml(_build)

    # ── Webhook parsing ───────────────────────────────────────

    def parse_webhook(self, form_data: dict) -> WebhookData:
        # Telnyx TeXML webhooks use the same field names as Twilio
        return WebhookData(
            call_sid=form_data.get("CallSid", ""),
            from_number=form_data.get("From", ""),
            to_number=form_data.get("To", ""),
            speech_result=form_data.get("SpeechResult", ""),
            confidence=form_data.get("Confidence", "0"),
            call_status=form_data.get("CallStatus", ""),
        )
