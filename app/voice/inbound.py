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
from app.integrations.local_store import (
    save_incomplete_booking,
    save_transcript as local_save_transcript,
)
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
    _custom = settings.greeting_message.strip()
    greeting = _custom if _custom else (
        f"Thank you for calling {settings.salon_name}! "
        f"I'm your booking assistant and I can help you schedule an appointment, "
        f"answer questions about our services, or connect you with our team. "
        f"How can I help you today?"
    )
    session.add_agent_message(greeting)

    return provider.build_gather(
        message=greeting,
        action_url="/voice/process-speech",
        timeout_url="/voice/process-speech",  # avoid destroying session on silence
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

    # No speech detected — re-ask the last question without polluting history
    if not wh.speech_result.strip():
        last_agent = ""
        for turn in reversed(session.history):
            if turn["role"] == "agent":
                last_agent = turn["content"]
                break
        prompt = last_agent or "I didn't catch that. Could you please repeat what you said?"
        return provider.build_gather(
            message=prompt,
            action_url="/voice/process-speech",
            timeout_url="/voice/process-speech",
            timeout_message="I'm still having trouble hearing you. Please try again.",
        )

    session.add_customer_message(wh.speech_result)

    # Ask AI / rule engine
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
            timeout_url="/voice/process-speech",  # preserve session on silence
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
        logger.error("Sheets logging failed [%s]: %s", type(exc).__name__, exc)

    try:
        send_booking_confirmation(appointment)
        send_staff_notification(appointment)
    except Exception as exc:
        logger.error("Email send failed [%s]: %s", type(exc).__name__, exc)

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
    """Log transcript, save incomplete booking if needed, update stats, clean up."""
    appt_dict = session.appointment.model_dump() if appointment_booked else None
    transcript_text = session.get_transcript()
    started_at = session.created_at.strftime("%Y-%m-%d %H:%M:%S")
    duration = session.duration_seconds()

    # ── 1. Always save to local JSON store (works without Sheets) ──
    try:
        local_save_transcript(
            call_sid=session.call_sid,
            call_type=session.call_type.value,
            from_number=session.from_number,
            to_number=session.to_number,
            started_at=started_at,
            duration_seconds=duration,
            transcript=transcript_text,
            appointment_booked=appointment_booked,
            appointment_data=appt_dict,
        )
    except Exception as exc:
        logger.warning("local_store.save_transcript failed [%s]: %s", type(exc).__name__, exc)

    # ── 2. If the call ended without a completed booking, save partial data ──
    if not appointment_booked:
        try:
            save_incomplete_booking(
                appointment=session.appointment,
                call_sid=session.call_sid,
                from_number=session.from_number,
                duration_seconds=duration,
            )
        except Exception as exc:
            logger.warning(
                "local_store.save_incomplete_booking failed [%s]: %s",
                type(exc).__name__, exc,
            )

    # ── 3. Also sync to Google Sheets (optional — skipped if not configured) ──
    try:
        record = TranscriptRecord(
            call_sid=session.call_sid,
            call_type=session.call_type,
            from_number=session.from_number,
            to_number=session.to_number,
            started_at=started_at,
            duration_seconds=duration,
            transcript=transcript_text,
            appointment_booked=appointment_booked,
            appointment_data=appt_dict,
        )
        log_transcript(record)
    except Exception as exc:
        logger.error("Failed to log transcript to Sheets [%s]: %s", type(exc).__name__, exc)

    # ── 4. Update call statistics for the Insights dashboard ──
    try:
        from app.ai.learner import record_call
        record_call(
            call_type="inbound",
            appointment_data=appt_dict,
            booked=appointment_booked,
        )
    except Exception as exc:
        logger.debug("Learner record_call skipped: %s", exc)
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
