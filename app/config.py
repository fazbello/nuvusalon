"""
Centralised configuration loaded from environment variables.
All secrets and tunables live here — nothing is hardcoded.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """App-wide settings. Every field can be overridden via env var."""

    # ── General ────────────────────────────────────────────────
    app_name: str = "NuvuSalon Voice Agent"
    debug: bool = False
    base_url: str = ""  # Public Railway URL, e.g. https://myapp.up.railway.app

    # ── Voice Provider ─────────────────────────────────────────
    voice_provider: str = "twilio"  # "twilio" | "telnyx" | "vapi"

    # ── Twilio VoIP ────────────────────────────────────────────
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""  # Your Twilio number in E.164

    # ── Telnyx VoIP ────────────────────────────────────────────
    telnyx_api_key: str = ""
    telnyx_phone_number: str = ""  # Your Telnyx number in E.164
    telnyx_app_id: str = ""  # TeXML Application ID

    # ── VAPI Voice AI ──────────────────────────────────────────
    vapi_api_key: str = ""
    vapi_phone_number: str = ""  # Display number
    vapi_phone_number_id: str = ""  # VAPI phone number resource ID
    vapi_server_secret: str = ""  # Webhook signature secret

    # ── Gemini AI ──────────────────────────────────────────────
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # ── Google Service Account (JSON string or file path) ──────
    google_service_account_json: str = ""  # Raw JSON string
    google_application_credentials: str = ""  # Path to .json file

    # ── Google Sheets ──────────────────────────────────────────
    google_sheet_id: str = ""  # Spreadsheet ID from URL

    # ── Google Calendar ────────────────────────────────────────
    google_calendar_id: str = "primary"
    appointment_duration_minutes: int = 60
    salon_timezone: str = "America/New_York"

    # ── Email (SendGrid) ───────────────────────────────────────
    sendgrid_api_key: str = ""
    from_email: str = ""  # Verified sender
    salon_notification_email: str = ""  # Where the salon receives alerts
    salon_name: str = "Nuvu Salon & Spa"

    # ── Knowledge base ─────────────────────────────────────────
    knowledge_base_path: str = "knowledge_base/salon_info.json"

    # ── Scheduler ──────────────────────────────────────────────
    reminder_hours_before: int = 24  # Send reminder N hours before appt

    # ── Voice / TTS ────────────────────────────────────────────
    tts_voice: str = "Polly.Joanna"  # Twilio <Say> voice
    speech_timeout: str = "auto"
    gather_timeout: int = 5
    language: str = "en-US"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    # ── Helpers ─────────────────────────────────────────────────

    def get_google_credentials_info(self) -> dict | None:
        """Return parsed service-account dict from env var or file."""
        if self.google_service_account_json:
            return json.loads(self.google_service_account_json)
        path = self.google_application_credentials
        if path and Path(path).exists():
            return json.loads(Path(path).read_text())
        return None


@lru_cache
def get_settings() -> Settings:
    base = Settings()
    # Apply non-secret overrides from config/salon_settings.json
    from app.settings_store import load_overrides
    overrides = load_overrides()
    if overrides:
        base = base.model_copy(update=overrides)
    return base
