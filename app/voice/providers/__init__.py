"""
Voice provider abstraction.

Supports Twilio, Telnyx, and VAPI as interchangeable telephony backends.
Set VOICE_PROVIDER env var to switch: "twilio" | "telnyx" | "vapi"
"""

from __future__ import annotations

from functools import lru_cache

from app.voice.providers.base import VoiceProvider


@lru_cache
def get_provider() -> VoiceProvider:
    """Return the configured voice provider singleton."""
    from app.config import get_settings

    settings = get_settings()
    name = settings.voice_provider.lower()

    if name == "twilio":
        from app.voice.providers.twilio_provider import TwilioProvider
        return TwilioProvider(settings)
    elif name == "telnyx":
        from app.voice.providers.telnyx_provider import TelnyxProvider
        return TelnyxProvider(settings)
    elif name == "vapi":
        from app.voice.providers.vapi_provider import VAPIProvider
        return VAPIProvider(settings)
    else:
        raise ValueError(
            f"Unknown voice provider: {name!r}. "
            "Supported: twilio, telnyx, vapi"
        )
