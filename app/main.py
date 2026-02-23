"""
NuvuSalon Voice Agent — FastAPI Application

Entrypoint for the salon AI phone agent that handles inbound/outbound
VoIP calls, books appointments, and manages customer communications.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app.ai.gemini_agent import research
from app.config import get_settings
from app.integrations.google_sheets import setup_spreadsheet
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


# ── Root Landing Page ──────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    """Landing page with system status and API docs link."""
    settings = get_settings()
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{settings.salon_name} — Voice Agent</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
           background: #0f0f1a; color: #e0e0e0; min-height: 100vh;
           display: flex; align-items: center; justify-content: center; }}
    .card {{ background: #1a1a2e; border-radius: 16px; padding: 48px;
             max-width: 520px; width: 90%; box-shadow: 0 8px 32px rgba(0,0,0,0.4); }}
    .badge {{ display: inline-block; background: #10b981; color: #fff; font-size: 12px;
              font-weight: 600; padding: 4px 12px; border-radius: 20px; margin-bottom: 16px;
              text-transform: uppercase; letter-spacing: 0.5px; }}
    h1 {{ font-size: 28px; margin-bottom: 8px;
          background: linear-gradient(135deg, #667eea, #764ba2);
          -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
    .sub {{ color: #888; margin-bottom: 32px; font-size: 15px; }}
    .status {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 32px; }}
    .status-item {{ background: #16213e; padding: 14px; border-radius: 10px; }}
    .status-item .label {{ font-size: 12px; color: #667; text-transform: uppercase;
                           letter-spacing: 0.5px; margin-bottom: 4px; }}
    .status-item .value {{ font-size: 14px; font-weight: 500; }}
    .dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%;
            margin-right: 6px; vertical-align: middle; }}
    .dot.on {{ background: #10b981; }}
    .dot.off {{ background: #ef4444; }}
    .links {{ display: flex; gap: 12px; }}
    .links a {{ flex: 1; text-align: center; padding: 12px; border-radius: 10px;
                text-decoration: none; font-weight: 500; font-size: 14px;
                transition: opacity 0.2s; }}
    .links a:hover {{ opacity: 0.85; }}
    .links .primary {{ background: linear-gradient(135deg, #667eea, #764ba2); color: #fff; }}
    .links .secondary {{ background: #16213e; color: #a0a0b8; }}
  </style>
</head>
<body>
  <div class="card">
    <span class="badge">Live</span>
    <h1>{settings.salon_name}</h1>
    <p class="sub">AI Voice Agent &mdash; Appointment Booking System</p>
    <div class="status">
      <div class="status-item">
        <div class="label">Twilio VoIP</div>
        <div class="value"><span class="dot {'on' if settings.twilio_account_sid else 'off'}"></span>
          {'Connected' if settings.twilio_account_sid else 'Not configured'}</div>
      </div>
      <div class="status-item">
        <div class="label">Gemini AI</div>
        <div class="value"><span class="dot {'on' if settings.gemini_api_key else 'off'}"></span>
          {'Connected' if settings.gemini_api_key else 'Not configured'}</div>
      </div>
      <div class="status-item">
        <div class="label">Google Sheets</div>
        <div class="value"><span class="dot {'on' if settings.google_sheet_id else 'off'}"></span>
          {'Connected' if settings.google_sheet_id else 'Not configured'}</div>
      </div>
      <div class="status-item">
        <div class="label">Email (SendGrid)</div>
        <div class="value"><span class="dot {'on' if settings.sendgrid_api_key else 'off'}"></span>
          {'Connected' if settings.sendgrid_api_key else 'Not configured'}</div>
      </div>
    </div>
    <div class="links">
      <a href="/docs" class="primary">API Docs</a>
      <a href="/health" class="secondary">Health Check</a>
    </div>
  </div>
</body>
</html>"""


# ── Health Check ───────────────────────────────────────────────

@app.get("/health")
async def health():
    """Railway health check endpoint."""
    settings = get_settings()
    return {
        "status": "healthy",
        "service": settings.app_name,
        "twilio_configured": bool(settings.twilio_account_sid),
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


@app.post("/api/setup-sheets")
async def api_setup_sheets():
    """Manually trigger Google Sheets setup."""
    try:
        url = setup_spreadsheet()
        return {"status": "ok", "url": url}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
