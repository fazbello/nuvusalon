"""
FastAPI router for all voice / VoIP endpoints.

Provider-agnostic: works with Twilio (form-data), Telnyx (form-data),
and VAPI (JSON). The active provider determines the response content type.

All webhook handlers are wrapped in try/except so that telephony providers
always receive valid call-control markup (TwiML / TeXML / JSON) instead of
an HTTP 500, which would cause the provider to play a generic error message.
"""

from __future__ import annotations

import logging
import traceback

from fastapi import APIRouter, Request, Response

from app.voice.inbound import handle_call_status, handle_inbound_call, handle_speech_input
from app.voice.outbound import (
    handle_outbound_answer,
    handle_outbound_speech,
    initiate_outbound_call,
)
from app.voice.providers import get_provider
from app.models.appointment import OutboundCallRequest

logger = logging.getLogger(__name__)
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


def _error_response(message: str = "We're experiencing a temporary issue. Please try calling again in a moment.") -> Response:
    """Return a valid TwiML/provider error response so the caller hears a message instead of Twilio's generic error."""
    try:
        provider = get_provider()
        body = provider.build_say_hangup(message)
        return Response(content=body, media_type=provider.content_type)
    except Exception:
        # Absolute fallback — raw TwiML
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            f"<Say>{message}</Say>"
            "<Hangup/>"
            "</Response>"
        )
        return Response(content=twiml, media_type="application/xml")


# ── Inbound ────────────────────────────────────────────────────

@router.post("/inbound")
async def inbound_call(request: Request):
    """Webhook: incoming call just arrived."""
    try:
        data = await _parse_request(request)
        logger.info(
            "Inbound call webhook received — From: %s  To: %s  CallSid: %s",
            data.get("From", "?"), data.get("To", "?"), data.get("CallSid", "?"),
        )
        response_body = await handle_inbound_call(data)
        return _provider_response(response_body)
    except Exception as exc:
        logger.error("Error handling inbound call: %s\n%s", exc, traceback.format_exc())
        return _error_response()


@router.post("/process-speech")
async def process_speech(request: Request):
    """Webhook: customer spoke during inbound call."""
    try:
        data = await _parse_request(request)
        logger.info(
            "Speech input — CallSid: %s  Speech: %r  Confidence: %s",
            data.get("CallSid", "?"), data.get("SpeechResult", ""), data.get("Confidence", "?"),
        )
        response_body = await handle_speech_input(data)
        return _provider_response(response_body)
    except Exception as exc:
        logger.error("Error processing speech: %s\n%s", exc, traceback.format_exc())
        return _error_response(
            "I'm sorry, I had trouble processing that. Could you please repeat what you said?"
        )


@router.post("/status")
async def call_status(request: Request):
    """Status callback: call state changed."""
    try:
        data = await _parse_request(request)
        await handle_call_status(data)
    except Exception as exc:
        logger.error("Error handling call status: %s\n%s", exc, traceback.format_exc())
    return Response(status_code=204)


# ── Outbound ───────────────────────────────────────────────────

@router.post("/outbound-call")
async def trigger_outbound_call(outbound_request: OutboundCallRequest, request: Request):
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
    from fastapi.responses import JSONResponse
    try:
        result = initiate_outbound_call(outbound_request, request_host=request.headers.get("host"))
        return result
    except Exception as exc:
        logger.error("Failed to initiate outbound call: %s\n%s", exc, traceback.format_exc())
        return JSONResponse(
            status_code=400,
            content={"error": str(exc), "detail": "Check BASE_URL and Twilio credentials."},
        )


@router.post("/outbound-answer")
async def outbound_answer(request: Request):
    """Webhook: outbound call was answered."""
    try:
        data = await _parse_request(request)
        logger.info(
            "Outbound call answered — CallSid: %s  To: %s  Status: %s",
            data.get("CallSid", "?"), data.get("To", "?"), data.get("CallStatus", "?"),
        )
        response_body = await handle_outbound_answer(data)
        return _provider_response(response_body)
    except Exception as exc:
        logger.error("Error handling outbound answer: %s\n%s", exc, traceback.format_exc())
        return _error_response(
            "Hello, we were calling from the salon but encountered a technical issue. We'll try again later. Goodbye."
        )


@router.post("/outbound-process")
async def outbound_process(request: Request):
    """Webhook: customer spoke during outbound call."""
    try:
        data = await _parse_request(request)
        response_body = await handle_outbound_speech(data)
        return _provider_response(response_body)
    except Exception as exc:
        logger.error("Error processing outbound speech: %s\n%s", exc, traceback.format_exc())
        return _error_response(
            "I'm sorry, I had trouble processing that. Could you please repeat?"
        )
