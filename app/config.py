"""
Centralised configuration loaded from environment variables.
All secrets and tunables live here — nothing is hardcoded.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


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

    # ── AI Provider ────────────────────────────────────────────
    ai_provider: str = "gemini"  # "gemini" | "openai"

    # ── Gemini AI ──────────────────────────────────────────────
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # ── OpenAI ─────────────────────────────────────────────────
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

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

    # ── Dashboard Auth ─────────────────────────────────────────
    dashboard_username: str = "admin"
    dashboard_password: str = ""  # Required to access dashboard
    dashboard_secret: str = ""    # HMAC signing key for session cookies

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


def get_base_url(request_host: str | None = None) -> str:
    """
    Return the public base URL used for constructing Twilio webhook callbacks.

    Resolution order (first non-empty wins):
      1. BASE_URL env var / settings.base_url
      2. RAILWAY_PUBLIC_DOMAIN env var (Railway sets this automatically)
      3. request_host extracted from the incoming HTTP request
      4. Empty string — logs a clear error so the operator knows what to fix

    The single most common cause of "We're sorry, an application error has occurred"
    on outbound calls is BASE_URL not being set.
    """
    settings = get_settings()

    if settings.base_url:
        url = settings.base_url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
            logger.warning(
                "BASE_URL %r is missing the https:// scheme — auto-corrected to %s. "
                "Update BASE_URL in Railway Variables to use the full URL.",
                settings.base_url, url,
            )
        return url

    # Railway automatically injects RAILWAY_PUBLIC_DOMAIN (e.g. "myapp.up.railway.app")
    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if railway_domain:
        url = f"https://{railway_domain.rstrip('/')}"
        logger.info(
            "BASE_URL not set — auto-detected Railway URL: %s  "
            "Set BASE_URL=%s in Railway variables to silence this log.",
            url, url,
        )
        return url

    if request_host:
        url = f"https://{request_host.rstrip('/')}"
        logger.warning(
            "BASE_URL and RAILWAY_PUBLIC_DOMAIN not set — "
            "using request Host header: %s. Add BASE_URL to Railway env vars.",
            url,
        )
        return url

    logger.error(
        "BASE_URL is not configured and RAILWAY_PUBLIC_DOMAIN is not available. "
        "Outbound call webhook URLs will be broken. "
        "Fix: add BASE_URL=https://your-app.up.railway.app in Railway > Variables."
    )
    return ""
