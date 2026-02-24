"""
NuvuSalon Voice Agent — FastAPI Application

Entrypoint for the salon AI phone agent that handles inbound/outbound
VoIP calls, books appointments, and manages customer communications.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app.ai.gemini_agent import research
from app.config import get_settings
from app.integrations.google_sheets import (
    get_appointments,
    get_transcripts,
    setup_spreadsheet,
)
from app.knowledge_base.loader import (
    get_full_kb,
    get_services_flat,
    get_technicians,
    get_technicians_for_service,
    reload as reload_kb,
)
from app.models.appointment import OutboundCallRequest
from app.scheduler.reminders import start_scheduler, stop_scheduler
from app.voice.router import router as voice_router
from app.voice.session import get_active_sessions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    settings = get_settings()
    logger.info("Starting %s", settings.app_name)

    # Initialize spreadsheet tabs
    if settings.google_sheet_id and settings.get_google_credentials_info():
        try:
            url = setup_spreadsheet()
            logger.info("Google Sheet ready: %s", url)
        except Exception as exc:
            logger.warning("Could not set up Google Sheet: %s", exc)

    # Start reminder scheduler
    try:
        start_scheduler()
    except Exception as exc:
        logger.warning("Could not start scheduler: %s", exc)

    yield

    stop_scheduler()
    logger.info("Shutting down %s", settings.app_name)


app = FastAPI(
    title="NuvuSalon Voice Agent",
    description=(
        "AI-powered VoIP phone agent for salon & spa appointment booking. "
        "Handles inbound/outbound calls via Twilio, uses Gemini for conversation, "
        "and integrates with Google Calendar, Sheets, and SendGrid."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow dashboard/admin front-ends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Voice Routes (Twilio webhooks) ─────────────────────────────
app.include_router(voice_router)


# ── Admin Dashboard ────────────────────────────────────────────

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "dashboard.html"


@app.get("/", response_class=HTMLResponse)
async def root():
    """Admin dashboard: outbound calls, transcripts, appointments, live status."""
    settings = get_settings()
    template = _TEMPLATE_PATH.read_text()

    def _dot(val: str) -> tuple[str, str]:
        return ("on", "Connected") if val else ("off", "Not configured")

    from app.voice.providers import get_provider
    try:
        provider = get_provider()
        voip_configured = provider.is_configured()
        voip_name = provider.name
    except Exception:
        voip_configured = False
        voip_name = settings.voice_provider.title()

    vp_dot, vp_lbl = ("on", "Connected") if voip_configured else ("off", "Not configured")
    gm_dot, gm_lbl = _dot(settings.gemini_api_key)
    sh_dot, sh_lbl = _dot(settings.google_sheet_id)
    em_dot, em_lbl = _dot(settings.sendgrid_api_key)

    html = (
        template
        .replace("{{salon_name}}", settings.salon_name)
        .replace("{{voip_name}}", voip_name)
        .replace("{{voip_dot}}", vp_dot)
        .replace("{{voip_label}}", vp_lbl)
        .replace("{{gemini_dot}}", gm_dot)
        .replace("{{gemini_label}}", gm_lbl)
        .replace("{{sheets_dot}}", sh_dot)
        .replace("{{sheets_label}}", sh_lbl)
        .replace("{{email_dot}}", em_dot)
        .replace("{{email_label}}", em_lbl)
    )
    return HTMLResponse(content=html)


# ── Health Check ───────────────────────────────────────────────

@app.get("/health")
async def health():
    """Railway health check endpoint."""
    settings = get_settings()
    from app.voice.providers import get_provider
    try:
        provider = get_provider()
        voip_ok = provider.is_configured()
        voip_name = provider.name
    except Exception:
        voip_ok = False
        voip_name = settings.voice_provider
    return {
        "status": "healthy",
        "service": settings.app_name,
        "voice_provider": voip_name,
        "voice_configured": voip_ok,
        "gemini_configured": bool(settings.gemini_api_key),
        "sheets_configured": bool(settings.google_sheet_id),
        "email_configured": bool(settings.sendgrid_api_key),
    }


# ── Dashboard / Admin API ─────────────────────────────────────

@app.get("/api/status")
async def get_status():
    """Overview of active calls and system status."""
    sessions = get_active_sessions()
    return {
        "active_calls": len(sessions),
        "calls": [
            {
                "call_sid": s.call_sid,
                "from": s.from_number,
                "type": s.call_type.value,
                "state": s.state.value,
                "duration_seconds": s.duration_seconds(),
                "data_collected": s.appointment.model_dump(),
            }
            for s in sessions.values()
        ],
    }


@app.get("/api/services")
async def list_services():
    """List all salon services from the knowledge base."""
    return {"services": get_services_flat()}


@app.get("/api/technicians")
async def list_technicians(service: str | None = None):
    """List technicians, optionally filtered by service."""
    if service:
        return {"technicians": get_technicians_for_service(service)}
    return {"technicians": get_technicians()}


@app.get("/api/knowledge-base")
async def get_knowledge_base():
    """Return the full knowledge base."""
    return get_full_kb()


@app.post("/api/knowledge-base/reload")
async def reload_knowledge_base():
    """Hot-reload the knowledge base from disk."""
    kb = reload_kb()
    return {"status": "reloaded", "sections": list(kb.keys())}


@app.post("/api/research")
async def research_endpoint(question: str):
    """Use Gemini to research a salon/spa industry question."""
    answer = await research(question)
    return {"question": question, "answer": answer}


@app.post("/api/outbound-call")
async def api_outbound_call(request: OutboundCallRequest):
    """
    Initiate an outbound call (alias for /voice/outbound-call).
    Useful for admin dashboards and automation.
    """
    from app.voice.outbound import initiate_outbound_call
    return initiate_outbound_call(request)


@app.get("/api/transcripts")
async def api_transcripts(limit: int = 50):
    """Fetch recent call transcripts from Google Sheets."""
    try:
        rows = get_transcripts(limit=limit)
        return {"transcripts": rows}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.get("/api/appointments")
async def api_appointments(limit: int = 50):
    """Fetch recent appointments from Google Sheets."""
    try:
        rows = get_appointments(limit=limit)
        return {"appointments": rows}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/api/setup-sheets")
async def api_setup_sheets():
    """Manually trigger Google Sheets setup."""
    try:
        url = setup_spreadsheet()
        return {"status": "ok", "url": url}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
