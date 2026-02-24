"""
Abstract base class for voice/telephony providers.

Each provider must implement:
  - Outbound call initiation (REST API call)
  - XML/response generation for call control (say, gather, hangup, dial)
  - Webhook form-data parsing (extract call_sid, speech, status, etc.)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CallResult:
    """Returned after initiating an outbound call."""
    call_sid: str
    status: str


@dataclass
class WebhookData:
    """Normalised fields extracted from any provider's webhook payload."""
    call_sid: str
    from_number: str
    to_number: str
    speech_result: str
    confidence: str
    call_status: str


class VoiceProvider(ABC):
    """Interface that every telephony backend must implement."""

    # ── Provider identity ──────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name, e.g. 'Twilio'."""

    @property
    @abstractmethod
    def phone_number(self) -> str:
        """The salon's phone number for this provider (E.164)."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if all required credentials are set."""

    # ── Outbound call ──────────────────────────────────────────

    @abstractmethod
    def initiate_call(
        self,
        to: str,
        answer_url: str,
        status_url: str,
    ) -> CallResult:
        """Place an outbound call. Returns call SID + status."""

    # ── Call control (XML / response builders) ─────────────────

    @abstractmethod
    def build_gather(
        self,
        message: str,
        action_url: str,
        timeout_url: str,
        timeout_message: str,
    ) -> str:
        """
        Build a response that speaks *message*, then listens for speech.
        On speech → POST to action_url.
        On silence → speak timeout_message then redirect to timeout_url.
        """

    @abstractmethod
    def build_say_hangup(self, *messages: str) -> str:
        """Speak one or more messages then hang up."""

    @abstractmethod
    def build_say_dial(self, message: str, dial_number: str) -> str:
        """Speak a message then connect to a human at dial_number."""

    # ── Webhook parsing ────────────────────────────────────────

    @abstractmethod
    def parse_webhook(self, form_data: dict) -> WebhookData:
        """
        Normalise the provider's webhook payload into WebhookData.
        Works for both inbound call arrival and speech-result callbacks.
        """

    # ── Response content type ──────────────────────────────────

    @property
    def content_type(self) -> str:
        """MIME type for call-control responses (XML for most)."""
        return "application/xml"
