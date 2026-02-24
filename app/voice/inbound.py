"""
Inbound call handler.

Provider-agnostic: works with Twilio, Telnyx, or VAPI.
The active VoIP provider is selected via VOICE_PROVIDER env var.
The active AI provider (Gemini or OpenAI) is selected via AI_PROVIDER env var.

Conversation loop:
  1. POST /voice/inbound          → greeting + first Gather
  2. POST /voice/process-speech   → AI response + next Gather (loops)
  3. POST /voice/status           → call ended, log transcript
"""

from __future__ import annotations

import logging

from app.ai.dispatcher import get_inbound_response
from app.config import get_settings
from app.integrations.email_sender import (
    send_booking_confirmation,
    send_staff_notification,
)
from app.integrations.google_calendar import create_appointment_event
from app.integrations.google_sheets import log_appointment, log_transcript
from app.models.appointment import CallType, TranscriptRecord
from app.voice.providers import get_provider
from app.voice.session import (
    CallSession,
    create_session,
    end_session,
    get_session,
)

logger = logging.getLogger(__name__)


async def handle_inbound_call(form_data: dict) -> str:
    """
    First webhook — a new call just arrived.
    Greet and start gathering speech.
    """
    provider = get_provider()
    wh = provider.parse_webhook(form_data)

    session = create_session(
        call_sid=wh.call_sid,
        from_number=wh.from_number,
        to_number=wh.to_number,
        call_type=CallType.INBOUND,
    )

    settings = get_settings()
    greeting = (
        f"Thank you for calling {settings.salon_name}! "
        f"I'm your booking assistant and I can help you schedule an appointment, "
        f"answer questions about our services, or connect you with our team. "
        f"How can I help you today?"
    )
    session.add_agent_message(greeting)

    return provider.build_gather(
        message=greeting,
        action_url="/voice/process-speech",
        timeout_url="/voice/inbound",
        timeout_message="I didn't catch that. Could you please repeat?",
    )


async def handle_speech_input(form_data: dict) -> str:
    """
    Looping webhook — customer said something.
    Feed to Gemini, get response, continue or book.
    """
    provider = get_provider()
    wh = provider.parse_webhook(form_data)

    session = get_session(wh.call_sid)
    if not session:
        # Session lost (restart, etc.) — recreate
        session = create_session(
            call_sid=wh.call_sid,
            from_number=wh.from_number,
            to_number=wh.to_number,
            call_type=CallType.INBOUND,
        )

    logger.info("Call %s — speech (%s confidence): %s", wh.call_sid, wh.confidence, wh.speech_result)
    session.add_customer_message(wh.speech_result)

    # Ask Gemini
    agent_response = await get_inbound_response(
        conversation_history=session.history,
        appointment=session.appointment,
    )

    # Merge extracted data
    if agent_response.extracted_data:
        session.update_appointment(agent_response.extracted_data)

    session.add_agent_message(agent_response.message)

    settings = get_settings()

    if agent_response.action == "book":
        return await _book_appointment(session, settings)

    elif agent_response.action == "end":
        await _finalize_call(session, appointment_booked=False)
        return provider.build_say_hangup(
            agent_response.message,
            f"Thank you for calling {settings.salon_name}. Goodbye!",
        )

    elif agent_response.action == "transfer":
        return provider.build_say_dial(
            agent_response.message,
            provider.phone_number,
        )

    else:
        # "continue" or "confirm" — keep gathering
        return provider.build_gather(
            message=agent_response.message,
            action_url="/voice/process-speech",
            timeout_url="/voice/inbound",
            timeout_message="I didn't catch that. Could you please repeat?",
        )


async def _book_appointment(session: CallSession, settings) -> str:
    """Process the actual booking: Calendar + Sheets + Email."""
    provider = get_provider()
    appointment = session.appointment
    calendar_link = ""

    try:
        event = create_appointment_event(appointment)
        calendar_link = event.get("htmlLink", "")
    except Exception as exc:
        logger.error("Calendar booking failed: %s", exc)

    try:
        log_appointment(appointment, calendar_link=calendar_link)
    except Exception as exc:
        logger.error("Sheets logging failed: %s", exc)

    try:
        send_booking_confirmation(appointment)
        send_staff_notification(appointment)
    except Exception as exc:
        logger.error("Email send failed: %s", exc)

    session.appointment_booked = True

    confirmation_msg = (
        f"Wonderful! Your appointment for {appointment.service} "
        f"on {appointment.preferred_date} at {appointment.preferred_time} "
        f"has been booked"
    )
    if appointment.technician:
        confirmation_msg += f" with {appointment.technician}"
    confirmation_msg += (
        ". You'll receive a confirmation email shortly. "
        f"Thank you for choosing {settings.salon_name}! Have a great day!"
    )

    await _finalize_call(session, appointment_booked=True)
    return provider.build_say_hangup(confirmation_msg)


async def _finalize_call(session: CallSession, appointment_booked: bool) -> None:
    """Log transcript and clean up session."""
    try:
        record = TranscriptRecord(
            call_sid=session.call_sid,
            call_type=session.call_type,
            from_number=session.from_number,
            to_number=session.to_number,
            started_at=session.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            duration_seconds=session.duration_seconds(),
            transcript=session.get_transcript(),
            appointment_booked=appointment_booked,
            appointment_data=session.appointment.model_dump() if appointment_booked else None,
        )
        log_transcript(record)
    except Exception as exc:
        logger.error("Failed to log transcript: %s", exc)
    finally:
        end_session(session.call_sid)


async def handle_call_status(form_data: dict) -> None:
    """Status callback — call ended, ensure we clean up."""
    provider = get_provider()
    wh = provider.parse_webhook(form_data)
    logger.info("Call %s status: %s", wh.call_sid, wh.call_status)

    if wh.call_status in ("completed", "failed", "busy", "no-answer", "canceled"):
        session = get_session(wh.call_sid)
        if session:
            await _finalize_call(session, appointment_booked=session.appointment_booked)
