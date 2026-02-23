"""
FastAPI router for all voice / VoIP endpoints.

Twilio webhooks are regular POST requests with form data.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request, Response

from app.voice.inbound import handle_call_status, handle_inbound_call, handle_speech_input
from app.voice.outbound import (
    handle_outbound_answer,
    handle_outbound_speech,
    initiate_outbound_call,
)
from app.models.appointment import OutboundCallRequest

router = APIRouter(prefix="/voice", tags=["voice"])


def _twiml_response(twiml: str) -> Response:
    """Return TwiML with correct content type."""
    return Response(content=twiml, media_type="application/xml")


# ── Inbound ────────────────────────────────────────────────────

@router.post("/inbound")
async def inbound_call(request: Request):
    """Twilio webhook: incoming call just arrived."""
    form = await request.form()
    twiml = await handle_inbound_call(dict(form))
    return _twiml_response(twiml)


@router.post("/process-speech")
async def process_speech(request: Request):
    """Twilio webhook: customer spoke during inbound call."""
    form = await request.form()
    twiml = await handle_speech_input(dict(form))
    return _twiml_response(twiml)


@router.post("/status")
async def call_status(request: Request):
    """Twilio status callback: call state changed."""
    form = await request.form()
    await handle_call_status(dict(form))
    return Response(status_code=204)


# ── Outbound ───────────────────────────────────────────────────

@router.post("/outbound-call")
async def trigger_outbound_call(request: OutboundCallRequest):
    """
    API endpoint to initiate an outbound call.

    Example:
        POST /voice/outbound-call
        {
            "phone_number": "+11234567890",
            "customer_name": "Jane Doe",
            "purpose": "appointment_confirmation",
            "appointment_details": {
                "service": "Haircut & Style",
                "date": "2026-03-01",
                "time": "14:00"
            }
        }
    """
    result = initiate_outbound_call(request)
    return result


@router.post("/outbound-answer")
async def outbound_answer(request: Request):
    """Twilio webhook: outbound call was answered."""
    form = await request.form()
    twiml = await handle_outbound_answer(dict(form))
    return _twiml_response(twiml)


@router.post("/outbound-process")
async def outbound_process(request: Request):
    """Twilio webhook: customer spoke during outbound call."""
    form = await request.form()
    twiml = await handle_outbound_speech(dict(form))
    return _twiml_response(twiml)
