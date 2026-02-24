"""
NuvuSalon Voice Agent — FastAPI Application

Entrypoint for the salon AI phone agent that handles inbound/outbound
VoIP calls, books appointments, and manages customer communications.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import date as _date
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.ai.dispatcher import research
from app.auth import check_credentials, get_session_user, login_response, logout_response
from app.config import get_base_url, get_settings
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
    save_kb,
    save_section,
)
from app.models.appointment import AppointmentData, OutboundCallRequest
from app.scheduler.reminders import start_scheduler, stop_scheduler
from app.settings_store import EDITABLE_KEYS, load_overrides, save_overrides
from app.voice.router import router as voice_router
from app.voice.session import get_active_sessions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_TEMPLATES = Path(__file__).parent / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    from app.ai.gemini_agent import _FALLBACK_MODEL, _KNOWN_MODELS

    settings = get_settings()
    logger.info("Starting %s", settings.app_name)

    # ── Startup configuration checks ──────────────────────────
    effective_base = get_base_url()
    if not effective_base:
        logger.error(
            "STARTUP WARNING: BASE_URL is not configured and cannot be auto-detected. "
            "Outbound calls will fail. Add BASE_URL=https://your-app.up.railway.app "
            "in Railway > Variables."
        )
    else:
        logger.info("Base URL: %s", effective_base)

    ai_provider = settings.ai_provider.lower()
    if ai_provider == "openai":
        if not settings.openai_api_key:
            logger.error("STARTUP WARNING: OPENAI_API_KEY is not set but ai_provider=openai. AI will fail.")
    else:
        if not settings.gemini_api_key:
            logger.error("STARTUP WARNING: GEMINI_API_KEY is not set. AI responses will fail.")
        if settings.gemini_model not in _KNOWN_MODELS:
            logger.warning(
                "STARTUP WARNING: gemini_model=%r is not a known valid model. "
                "Will fall back to %s. Fix via dashboard Configure > Voice & AI.",
                settings.gemini_model, _FALLBACK_MODEL,
            )

    if not settings.twilio_account_sid and settings.voice_provider == "twilio":
        logger.warning("STARTUP WARNING: TWILIO_ACCOUNT_SID is not set.")

    if not settings.dashboard_password:
        logger.warning(
            "STARTUP WARNING: DASHBOARD_PASSWORD is not set — dashboard login is disabled. "
            "Add DASHBOARD_PASSWORD=<your-password> in Railway > Variables."
        )

    # Initialize spreadsheet tabs
    if settings.google_sheet_id and settings.get_google_credentials_info():
        try:
            url = setup_spreadsheet()
            logger.info("Google Sheet ready: %s", url)
        except Exception as exc:
            logger.warning("Could not set up Google Sheet: %s", exc)
    elif settings.google_sheet_id and not settings.get_google_credentials_info():
        logger.warning(
            "STARTUP WARNING: GOOGLE_SHEET_ID is set but no Google credentials found. "
            "Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS."
        )

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
        "Handles inbound/outbound calls via Twilio, uses Gemini or OpenAI for conversation, "
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


# ── Public Landing Page ────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    """Public salon landing page with online booking form."""
    settings = get_settings()
    template = (_TEMPLATES / "index.html").read_text()

    # Build service options for the dropdown
    services = get_services_flat()
    service_options = "\n".join(
        f'<option value="{s}">{s}</option>' for s in services
    ) if services else '<option value="Haircut">Haircut</option>'

    phone = settings.twilio_phone_number or ""
    phone_display = phone if phone else "our salon"

    phone_nav = (
        f'<a href="tel:{phone}" class="tel-link">&#128222; {phone}</a>'
        if phone else ""
    )
    phone_btn = (
        f'<a href="tel:{phone}" class="btn-hero ghost">&#128222; Call Us</a>'
        if phone else ""
    )

    html = (
        template
        .replace("{{salon_name}}", settings.salon_name)
        .replace("{{service_options}}", service_options)
        .replace("{{phone_number}}", phone)
        .replace("{{phone_number_display}}", phone_display)
        .replace("{{phone_nav}}", phone_nav)
        .replace("{{phone_btn}}", phone_btn)
        .replace("{{year}}", str(_date.today().year))
    )
    return HTMLResponse(content=html)


# ── Twilio misconfiguration safety net ────────────────────────

@app.post("/")
async def root_post_fallback(request: Request):
    """
    Twilio sometimes POSTs to '/' when the webhook URL is misconfigured.
    Redirect to the inbound handler so the caller still hears a greeting.
    """
    try:
        form = await request.form()
        body = dict(form)
    except Exception:
        body = {}
    logger.warning(
        "Twilio hit POST / instead of a voice webhook — probable cause: "
        "BASE_URL or RAILWAY_PUBLIC_DOMAIN not set. Payload: %s", body
    )
    return RedirectResponse(url="/voice/inbound", status_code=307)


# ── Auth: Login / Logout ───────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    """Staff login page."""
    # Already authenticated — go straight to dashboard
    if get_session_user(request):
        return RedirectResponse(url="/dashboard", status_code=302)

    settings = get_settings()
    template = (_TEMPLATES / "login.html").read_text()
    html = (
        template
        .replace("{{salon_name}}", settings.salon_name)
        .replace("{{error_message}}", error)
        .replace("{{error_class}}", "show" if error else "")
        .replace("{{prefill_username}}", "")
    )
    return HTMLResponse(content=html)


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """Process login form. Set session cookie on success, show error on failure."""
    if check_credentials(username, password):
        logger.info("Dashboard login: %s", username)
        return login_response(username, redirect_to="/dashboard")

    logger.warning("Dashboard login failed for username: %s", username)
    settings = get_settings()
    template = (_TEMPLATES / "login.html").read_text()
    html = (
        template
        .replace("{{salon_name}}", settings.salon_name)
        .replace("{{error_message}}", "Invalid username or password.")
        .replace("{{error_class}}", "show")
        .replace("{{prefill_username}}", username)
    )
    return HTMLResponse(content=html, status_code=401)


@app.get("/logout")
async def logout():
    """Clear session cookie and redirect to login."""
    return logout_response(redirect_to="/login")


# ── Admin Dashboard (auth required) ───────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Admin dashboard — requires login."""
    if not get_session_user(request):
        return RedirectResponse(url="/login", status_code=302)

    settings = get_settings()
    template = (_TEMPLATES / "dashboard.html").read_text()

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
    sh_dot, sh_lbl = _dot(settings.google_sheet_id)
    em_dot, em_lbl = _dot(settings.sendgrid_api_key)

    # AI provider status
    ai_provider = settings.ai_provider.lower()
    if ai_provider == "openai":
        ai_configured = bool(settings.openai_api_key)
        ai_name = "OpenAI"
    else:
        ai_configured = bool(settings.gemini_api_key)
        ai_name = "Gemini"
    ai_dot, ai_lbl = ("on", "Connected") if ai_configured else ("off", "Not configured")

    # BASE_URL warning
    effective_base = (
        settings.base_url
        or os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
        or request.headers.get("host", "")
    )
    base_url_warning = "" if effective_base else (
        '<div style="background:#f59e0b20;border:1px solid #f59e0b;border-radius:10px;'
        'padding:14px 20px;margin-bottom:20px;color:#f59e0b;font-size:13px;">'
        '<strong>&#9888; BASE_URL not configured</strong> — Outbound call webhooks will fail. '
        'Add <code style="background:#0005;padding:2px 6px;border-radius:4px;">'
        'BASE_URL=https://your-app.up.railway.app</code> '
        'in Railway &rsaquo; Variables.</div>'
    )

    html = (
        template
        .replace("{{salon_name}}", settings.salon_name)
        .replace("{{voip_name}}", voip_name)
        .replace("{{voip_dot}}", vp_dot)
        .replace("{{voip_label}}", vp_lbl)
        .replace("{{ai_provider_name}}", ai_name)
        .replace("{{ai_dot}}", ai_dot)
        .replace("{{ai_label}}", ai_lbl)
        .replace("{{sheets_dot}}", sh_dot)
        .replace("{{sheets_label}}", sh_lbl)
        .replace("{{email_dot}}", em_dot)
        .replace("{{email_label}}", em_lbl)
        .replace("{{base_url_warning}}", base_url_warning)
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

    ai_provider = settings.ai_provider.lower()
    ai_configured = bool(settings.openai_api_key) if ai_provider == "openai" else bool(settings.gemini_api_key)

    return {
        "status": "healthy",
        "service": settings.app_name,
        "voice_provider": voip_name,
        "voice_configured": voip_ok,
        "ai_provider": ai_provider,
        "ai_configured": ai_configured,
        "sheets_configured": bool(settings.google_sheet_id),
        "email_configured": bool(settings.sendgrid_api_key),
    }


# ── Webhook URL diagnostic ─────────────────────────────────────

@app.get("/api/webhook-urls")
async def webhook_urls(request: Request):
    """Returns the exact URLs to paste into the Twilio console."""
    settings = get_settings()
    host = request.headers.get("host", "")

    if settings.base_url:
        raw = settings.base_url.strip().rstrip("/")
        base = raw if raw.startswith(("http://", "https://")) else f"https://{raw}"
        source = "BASE_URL setting"
    elif os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
        base = f"https://{os.environ['RAILWAY_PUBLIC_DOMAIN'].rstrip('/')}"
        source = "RAILWAY_PUBLIC_DOMAIN env var (auto-detected)"
    elif host:
        base = f"https://{host.rstrip('/')}"
        source = "HTTP Host header (unreliable — set BASE_URL)"
    else:
        base = ""
        source = "NOT CONFIGURED — set BASE_URL in Railway Variables"

    return {
        "base_url": base,
        "source": source,
        "configured": bool(base),
        "twilio_console_settings": {
            "inbound_webhook": {
                "url": f"{base}/voice/inbound",
                "method": "HTTP POST",
                "description": "Paste into: Twilio Console → Phone Numbers → your number → Voice → A call comes in",
            },
            "status_callback": {
                "url": f"{base}/voice/status",
                "method": "HTTP POST",
                "description": "Optional: paste into Call Status Callback URL",
            },
        },
        "note": (
            "If base_url shows 'NOT CONFIGURED', add BASE_URL=https://your-app.up.railway.app "
            "in Railway → your service → Variables, then redeploy."
        ) if not base else "URLs look correct. Copy inbound_webhook URL into Twilio Console.",
    }


# ── Public Booking API ─────────────────────────────────────────

@app.post("/api/book")
async def api_book(request: Request):
    """
    Public online booking endpoint (used by the landing page form).
    Logs the request to Google Sheets and sends a staff notification email.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    try:
        appt = AppointmentData(
            customer_name=body.get("customer_name", "").strip() or None,
            phone_number=body.get("phone_number", "").strip() or None,
            email=body.get("email", "").strip() or None,
            service=body.get("service", "").strip() or None,
            preferred_date=body.get("preferred_date", "").strip() or None,
            preferred_time=body.get("preferred_time", "").strip() or None,
            notes=body.get("notes", "").strip() or None,
        )
    except Exception as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})

    missing = appt.missing_required_fields()
    if missing:
        return JSONResponse(
            status_code=422,
            content={"error": f"Missing required fields: {', '.join(missing)}"},
        )

    # Log to Google Sheets
    try:
        from app.integrations.google_sheets import log_appointment
        log_appointment(appt, calendar_link="")
    except Exception as exc:
        logger.warning("Online booking — failed to log to Sheets: %s", exc)

    # Notify staff by email
    try:
        from app.integrations.email_sender import send_staff_notification
        send_staff_notification(appt)
    except Exception as exc:
        logger.warning("Online booking — failed to send staff email: %s", exc)

    settings = get_settings()
    logger.info(
        "Online booking request: %s (%s) — %s on %s at %s",
        appt.customer_name, appt.phone_number, appt.service,
        appt.preferred_date, appt.preferred_time,
    )

    return {
        "status": "received",
        "message": (
            f"Thank you {appt.customer_name}! Your request for {appt.service} "
            f"on {appt.preferred_date} at {appt.preferred_time} has been received. "
            f"We'll confirm your appointment shortly."
        ),
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


@app.get("/api/insights")
async def api_insights():
    """Call statistics and learning data for the Insights dashboard tab."""
    try:
        from app.ai.learner import get_stats
        return get_stats()
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.get("/api/unknown-phrases")
async def api_unknown_phrases(limit: int = 50):
    """Unknown caller phrases logged for admin review / FAQ improvement."""
    try:
        from app.ai.learner import get_unknown_phrases
        return {"phrases": get_unknown_phrases(limit=limit)}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/api/unknown-phrases/review")
async def api_mark_reviewed(request: Request):
    """Mark an unknown phrase as reviewed so it leaves the action list."""
    try:
        body = await request.json()
        phrase = body.get("phrase", "")
        if phrase:
            from app.ai.learner import mark_reviewed
            mark_reviewed(phrase)
        return {"status": "ok"}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/api/research")
async def research_endpoint(question: str):
    """Use the active AI to research a salon/spa industry question."""
    answer = await research(question)
    return {"question": question, "answer": answer}


@app.post("/api/test-ai")
async def test_ai_connection():
    """
    Verify the active AI provider credentials with a minimal API call.
    Uses the cheapest/fastest path to reduce free-credit spend:
      - OpenAI: gpt-4o-mini, max_tokens=5
      - Gemini: gemini-2.0-flash (or configured model), max_output_tokens=5
    """
    settings = get_settings()
    provider = settings.ai_provider.lower()

    if provider == "openai":
        if not settings.openai_api_key:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "provider": "openai", "error": "OPENAI_API_KEY is not set in Railway Variables."},
            )
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=settings.openai_api_key)
            resp = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[{"role": "user", "content": "Reply with exactly one word: OK"}],
                max_tokens=5,
                temperature=0,
            )
            reply = (resp.choices[0].message.content or "").strip()
            tokens_used = resp.usage.total_tokens if resp.usage else "?"
            return {
                "ok": True,
                "provider": "openai",
                "model": settings.openai_model,
                "reply": reply,
                "tokens_used": tokens_used,
                "note": f"gpt-4o-mini costs ~$0.15/1M input tokens — very low free-credit usage.",
            }
        except Exception as exc:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "provider": "openai", "model": settings.openai_model, "error": str(exc)},
            )
    else:
        # Gemini
        if not settings.gemini_api_key:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "provider": "gemini", "error": "GEMINI_API_KEY is not set in Railway Variables."},
            )
        try:
            from google import genai
            from google.genai import types as gtypes
            from app.ai.gemini_agent import _FALLBACK_MODEL, _KNOWN_MODELS
            model = settings.gemini_model if settings.gemini_model in _KNOWN_MODELS else _FALLBACK_MODEL
            client = genai.Client(api_key=settings.gemini_api_key)
            resp = client.models.generate_content(
                model=model,
                contents="Reply with exactly one word: OK",
                config=gtypes.GenerateContentConfig(max_output_tokens=5, temperature=0),
            )
            reply = (resp.text or "").strip()
            return {
                "ok": True,
                "provider": "gemini",
                "model": model,
                "reply": reply,
                "note": "gemini-2.0-flash has a free tier — no billing needed for testing.",
            }
        except Exception as exc:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "provider": "gemini", "model": settings.gemini_model, "error": str(exc)},
            )


@app.post("/api/outbound-call")
async def api_outbound_call(outbound_request: OutboundCallRequest, request: Request):
    """
    Initiate an outbound call (alias for /voice/outbound-call).
    Useful for admin dashboards and automation.
    """
    from app.voice.outbound import initiate_outbound_call
    try:
        return initiate_outbound_call(outbound_request, request_host=request.headers.get("host"))
    except Exception as exc:
        logger.error("Outbound call failed: %s", exc)
        return JSONResponse(
            status_code=400,
            content={"error": str(exc), "detail": "Check BASE_URL and Twilio/provider credentials."},
        )


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


# ── Configuration API (franchise dashboard) ───────────────────

@app.get("/api/settings")
async def get_operational_settings():
    """Return current editable settings (overrides + effective values)."""
    settings = get_settings()
    overrides = load_overrides()
    effective = {}
    for key in sorted(EDITABLE_KEYS):
        effective[key] = {
            "value": getattr(settings, key, None),
            "overridden": key in overrides,
        }
    return {"settings": effective, "overrides": overrides}


@app.put("/api/settings")
async def update_operational_settings(request: Request):
    """Bulk-update editable settings."""
    body = await request.json()
    saved = save_overrides(body)
    return {"status": "saved", "settings": saved}


@app.get("/api/kb")
async def api_get_kb():
    """Return the full knowledge base."""
    return get_full_kb()


@app.put("/api/kb")
async def api_put_kb(request: Request):
    """Replace the entire knowledge base."""
    body = await request.json()
    kb = save_kb(body)
    return {"status": "saved", "sections": list(kb.keys())}


@app.get("/api/kb/{section}")
async def api_get_kb_section(section: str):
    """Return a single KB section."""
    kb = get_full_kb()
    if section not in kb:
        return JSONResponse(status_code=404, content={"error": f"Section '{section}' not found"})
    return {section: kb[section]}


@app.put("/api/kb/{section}")
async def api_put_kb_section(section: str, request: Request):
    """Update a single KB section."""
    body = await request.json()
    kb = save_section(section, body)
    return {"status": "saved", "section": section, "sections": list(kb.keys())}
