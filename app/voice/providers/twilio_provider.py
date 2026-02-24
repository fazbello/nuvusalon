"""Twilio telephony provider."""

from __future__ import annotations

import logging

from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import Gather, VoiceResponse

from app.voice.providers.base import CallResult, VoiceProvider, WebhookData

logger = logging.getLogger(__name__)


class TwilioProvider(VoiceProvider):

    def __init__(self, settings):
        self._settings = settings
        self._client: TwilioClient | None = None

    def _get_client(self) -> TwilioClient:
        if self._client is None:
            self._client = TwilioClient(
                self._settings.twilio_account_sid,
                self._settings.twilio_auth_token,
            )
        return self._client

    # ── Identity ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "Twilio"

    @property
    def phone_number(self) -> str:
        return self._settings.twilio_phone_number

    def is_configured(self) -> bool:
        return bool(
            self._settings.twilio_account_sid
            and self._settings.twilio_auth_token
            and self._settings.twilio_phone_number
        )

    # ── Outbound ──────────────────────────────────────────────

    def initiate_call(self, to: str, answer_url: str, status_url: str) -> CallResult:
        client = self._get_client()
        call = client.calls.create(
            to=to,
            from_=self.phone_number,
            url=answer_url,
            status_callback=status_url,
            status_callback_event=["completed", "failed", "busy", "no-answer"],
            status_callback_method="POST",
        )
        return CallResult(call_sid=call.sid, status=call.status)

    # ── Call control ──────────────────────────────────────────

    def build_gather(
        self,
        message: str,
        action_url: str,
        timeout_url: str,
        timeout_message: str,
    ) -> str:
        s = self._settings
        response = VoiceResponse()
        gather = Gather(
            input="speech",
            action=action_url,
            method="POST",
            timeout=s.gather_timeout,
            speech_timeout=s.speech_timeout,
            language=s.language,
        )
        gather.say(message, voice=s.tts_voice)
        response.append(gather)
        response.say(timeout_message, voice=s.tts_voice)
        response.redirect(timeout_url, method="POST")
        return str(response)

    def build_say_hangup(self, *messages: str) -> str:
        s = self._settings
        response = VoiceResponse()
        for msg in messages:
            response.say(msg, voice=s.tts_voice)
        response.hangup()
        return str(response)

    def build_say_dial(self, message: str, dial_number: str) -> str:
        s = self._settings
        response = VoiceResponse()
        response.say(message, voice=s.tts_voice)
        response.say(
            "Let me connect you with a team member. Please hold.",
            voice=s.tts_voice,
        )
        response.dial(dial_number)
        return str(response)

    # ── Webhook parsing ───────────────────────────────────────

    def parse_webhook(self, form_data: dict) -> WebhookData:
        return WebhookData(
            call_sid=form_data.get("CallSid", ""),
            from_number=form_data.get("From", ""),
            to_number=form_data.get("To", ""),
            speech_result=form_data.get("SpeechResult", ""),
            confidence=form_data.get("Confidence", "0"),
            call_status=form_data.get("CallStatus", ""),
        )
