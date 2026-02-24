"""
FastAPI router for all voice / VoIP endpoints.

Provider-agnostic: works with Twilio (form-data), Telnyx (form-data),
and VAPI (JSON). The active provider determines the response content type.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.voice.inbound import handle_call_status, handle_inbound_call, handle_speech_input
from app.voice.outbound import (
    handle_outbound_answer,
    handle_outbound_speech,
    initiate_outbound_call,
)
from app.voice.providers import get_provider
from app.models.appointment import OutboundCallRequest

router = APIRouter(prefix="/voice", tags=["voice"])


async def _parse_request(request: Request) -> dict:
    """
    Extract webhook data from any provider.
    Twilio/Telnyx send form-encoded POST data.
    VAPI sends JSON.
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        return await request.json()
    form = await request.form()
    return dict(form)


def _provider_response(body: str) -> Response:
    """Return a response with the active provider's content type."""
    provider = get_provider()
    return Response(content=body, media_type=provider.content_type)


# ── Inbound ────────────────────────────────────────────────────

@router.post("/inbound")
async def inbound_call(request: Request):
    """Webhook: incoming call just arrived."""
    data = await _parse_request(request)
    response_body = await handle_inbound_call(data)
    return _provider_response(response_body)


@router.post("/process-speech")
async def process_speech(request: Request):
    """Webhook: customer spoke during inbound call."""
    data = await _parse_request(request)
    response_body = await handle_speech_input(data)
    return _provider_response(response_body)


@router.post("/status")
async def call_status(request: Request):
    """Status callback: call state changed."""
    data = await _parse_request(request)
    await handle_call_status(data)
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
    """Webhook: outbound call was answered."""
    data = await _parse_request(request)
    response_body = await handle_outbound_answer(data)
    return _provider_response(response_body)


@router.post("/outbound-process")
async def outbound_process(request: Request):
    """Webhook: customer spoke during outbound call."""
    data = await _parse_request(request)
    response_body = await handle_outbound_speech(data)
    return _provider_response(response_body)
