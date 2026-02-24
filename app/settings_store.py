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
    # AI model
    "gemini_model",
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


def save_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """
    Replace all overrides with *data* (filtered to EDITABLE_KEYS).
    Returns the saved dict.
    """
    safe = {k: v for k, v in data.items() if k in EDITABLE_KEYS}
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
    """Clear cached singletons so new settings take effect."""
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        from app.voice.providers import get_provider
        get_provider.cache_clear()
    except Exception:
        pass
