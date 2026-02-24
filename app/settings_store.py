"""
Runtime-editable non-secret settings overlay.

Persisted to config/salon_settings.json so that franchise operators can
customise the system from the dashboard without touching .env files or code.

Only whitelisted keys (EDITABLE_KEYS) are accepted — API secrets, database
credentials, and other sensitive values are never stored here.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SETTINGS_FILE = Path("config/salon_settings.json")

EDITABLE_KEYS: set[str] = {
    # General
    "salon_name",
    "base_url",
    # Voice provider selection
    "voice_provider",
    # Voice / TTS tuning
    "tts_voice",
    "speech_timeout",
    "gather_timeout",
    "language",
    # AI provider & models
    "ai_provider",
    "gemini_model",
    "openai_model",
    # Booking
    "appointment_duration_minutes",
    "salon_timezone",
    "reminder_hours_before",
    "google_calendar_id",
    # Email (non-secret sender addresses)
    "from_email",
    "salon_notification_email",
    # Knowledge base path
    "knowledge_base_path",
}


def load_overrides() -> dict[str, Any]:
    """Load non-secret overrides from the JSON file."""
    if not SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        # Only return whitelisted keys
        return {k: v for k, v in data.items() if k in EDITABLE_KEYS}
    except Exception as exc:
        logger.warning("Could not load settings overlay: %s", exc)
        return {}


def _sanitize(data: dict[str, Any]) -> dict[str, Any]:
    """
    Normalise values before saving to prevent common operator mistakes.
    - base_url: strip whitespace/trailing slashes, prepend https:// if missing
    - gemini_model: warn if not a known valid model
    """
    out = dict(data)

    if "base_url" in out and isinstance(out["base_url"], str):
        url = out["base_url"].strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            url = f"https://{url}"
            logger.warning(
                "base_url saved without scheme — auto-corrected to %s. "
                "Update the field to include the full https:// URL.", url
            )
        out["base_url"] = url

    if "gemini_model" in out and isinstance(out["gemini_model"], str):
        known = {
            "gemini-2.0-flash", "gemini-2.0-flash-exp",
            "gemini-1.5-flash", "gemini-1.5-flash-8b", "gemini-1.5-pro",
        }
        model = out["gemini_model"].strip()
        if model and model not in known:
            logger.warning(
                "gemini_model %r is not a recognised model name. "
                "The agent will fall back to gemini-2.0-flash if the API rejects it. "
                "Valid options: %s", model, ", ".join(sorted(known))
            )
        out["gemini_model"] = model

    if "openai_model" in out and isinstance(out["openai_model"], str):
        known = {
            "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4",
            "gpt-3.5-turbo", "o1", "o1-mini", "o3-mini",
        }
        model = out["openai_model"].strip()
        if model and model not in known:
            logger.warning(
                "openai_model %r is not a standard OpenAI model name. "
                "Proceeding anyway — check https://platform.openai.com/docs/models",
                model,
            )
        out["openai_model"] = model

    if "ai_provider" in out and isinstance(out["ai_provider"], str):
        provider = out["ai_provider"].strip().lower()
        if provider not in ("gemini", "openai"):
            logger.warning("ai_provider %r unknown — defaulting to gemini", provider)
            provider = "gemini"
        out["ai_provider"] = provider

    return out


def save_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """
    Replace all overrides with *data* (filtered to EDITABLE_KEYS).
    Returns the saved dict.
    """
    safe = {k: v for k, v in data.items() if k in EDITABLE_KEYS}
    safe = _sanitize(safe)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(safe, indent=2))
    _invalidate_caches()
    return safe


def update_setting(key: str, value: Any) -> dict[str, Any]:
    """Update a single setting and return the full overrides dict."""
    if key not in EDITABLE_KEYS:
        raise ValueError(f"Setting {key!r} is not editable from the dashboard")
    current = load_overrides()
    current[key] = value
    return save_overrides(current)


def delete_setting(key: str) -> dict[str, Any]:
    """Remove an override (reverts to env/default). Returns remaining overrides."""
    current = load_overrides()
    current.pop(key, None)
    return save_overrides(current)


def _invalidate_caches() -> None:
    """Clear cached singletons so new settings take effect immediately."""
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        from app.voice.providers import get_provider
        get_provider.cache_clear()
    except Exception:
        pass
    try:
        from app.ai.gemini_agent import reset_client as _gr
        _gr()
    except Exception:
        pass
    try:
        from app.ai.openai_agent import reset_client as _or
        _or()
    except Exception:
        pass
