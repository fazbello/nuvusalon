"""
Outbound call handler.

Provider-agnostic: works with Twilio, Telnyx, or VAPI.
Triggers calls to customers for appointment confirmations,
reminders, and follow-ups.
"""

from __future__ import annotations

import json
import logging

from app.ai.gemini_agent import get_outbound_response
from app.config import get_base_url, get_settings
from app.models.appointment import CallType, OutboundCallRequest, TranscriptRecord
from app.integrations.google_sheets import log_transcript
from app.voice.providers import get_provider
from app.voice.session import (
    create_session,
    end_session,
    get_session,
)

logger = logging.getLogger(__name__)


def initiate_outbound_call(request: OutboundCallRequest, request_host: str | None = None) -> dict:
    """
    Start an outbound call via the configured provider.
    Returns the call SID and status.

    request_host is the HTTP Host header so webhook URLs can be built
    correctly when BASE_URL is not set in the environment.
    """
    provider = get_provider()

    base = get_base_url(request_host)
    answer_url = f"{base}/voice/outbound-answer"
    status_url = f"{base}/voice/status"

    result = provider.initiate_call(
        to=request.phone_number,
        answer_url=answer_url,
        status_url=status_url,
    )

    # Pre-create session so context is ready when the call connects
    context = ""
    if request.appointment_details:
        context = json.dumps(request.appointment_details)
    elif request.custom_message:
        context = request.custom_message

    create_session(
        call_sid=result.call_sid,
        from_number=provider.phone_number,
        to_number=request.phone_number,
        call_type=CallType.OUTBOUND,
        purpose=request.purpose,
        context=context,
    )

    logger.info(
        "Outbound call initiated via %s: %s → %s (SID: %s)",
        provider.name, provider.phone_number, request.phone_number, result.call_sid,
    )

    return {
        "call_sid": result.call_sid,
        "status": result.status,
        "to": request.phone_number,
        "purpose": request.purpose,
        "provider": provider.name,
    }


async def handle_outbound_answer(form_data: dict) -> str:
    """
    Webhook hit when the outbound call is answered.
    Deliver the opening message based on call purpose.
    """
    provider = get_provider()
    wh = provider.parse_webhook(form_data)
    settings = get_settings()
    base = get_base_url()

    session = get_session(wh.call_sid)
    if not session:
        return provider.build_say_hangup(
            f"Hello, this is {settings.salon_name}. We apologize, but we encountered "
            "a technical issue. We'll call you back shortly. Goodbye.",
        )

    # Get the opening message from Gemini
    agent_response = await get_outbound_response(
        conversation_history=[],
        purpose=session.purpose,
        context=session.context,
    )

    session.add_agent_message(agent_response.message)

    return provider.build_gather(
        message=agent_response.message,
        action_url=f"{base}/voice/outbound-process",
        timeout_url=f"{base}/voice/outbound-answer",
        timeout_message="I didn't hear a response. I'll try again.",
    )


async def handle_outbound_speech(form_data: dict) -> str:
    """
    Process customer speech during an outbound call.
    """
    provider = get_provider()
    wh = provider.parse_webhook(form_data)
    settings = get_settings()
    base = get_base_url()

    session = get_session(wh.call_sid)
    if not session:
        return provider.build_say_hangup("Thank you. Goodbye.")

    session.add_customer_message(wh.speech_result)

    agent_response = await get_outbound_response(
        conversation_history=session.history,
        purpose=session.purpose,
        context=session.context,
    )

    if agent_response.extracted_data:
        session.update_appointment(agent_response.extracted_data)

    session.add_agent_message(agent_response.message)

    if agent_response.action == "end":
        await _finalize_outbound(session)
        return provider.build_say_hangup(
            agent_response.message,
            "Thank you, and have a wonderful day! Goodbye.",
        )

    # Continue conversation
    return provider.build_gather(
        message=agent_response.message,
        action_url=f"{base}/voice/outbound-process",
        timeout_url=f"{base}/voice/outbound-answer",
        timeout_message="I didn't catch that. Could you say that again?",
    )


async def _finalize_outbound(session) -> None:
    """Log outbound call transcript."""
    try:
        record = TranscriptRecord(
            call_sid=session.call_sid,
            call_type=session.call_type,
            from_number=session.from_number,
            to_number=session.to_number,
            started_at=session.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            duration_seconds=session.duration_seconds(),
            transcript=session.get_transcript(),
            appointment_booked=session.appointment_booked,
        )
        log_transcript(record)
    except Exception as exc:
        logger.error("Failed to log outbound transcript: %s", exc)
    finally:
        end_session(session.call_sid)
