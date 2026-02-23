"""
Inbound call handler.

Twilio sends webhooks here when a call arrives on the salon number.
We use <Gather input="speech"> for voice recognition, feed transcripts
to Gemini, and respond with TwiML <Say>.

Conversation loop:
  1. POST /voice/inbound          → greeting + first Gather
  2. POST /voice/process-speech   → Gemini response + next Gather (loops)
  3. POST /voice/status           → call ended, log transcript
"""

from __future__ import annotations

import logging
from datetime import datetime

from twilio.twiml.voice_response import Gather, VoiceResponse

from app.ai.gemini_agent import get_inbound_response
from app.config import get_settings
from app.integrations.email_sender import (
    send_booking_confirmation,
    send_staff_notification,
)
from app.integrations.google_calendar import create_appointment_event
from app.integrations.google_sheets import log_appointment, log_transcript
from app.models.appointment import CallType, ConversationState, TranscriptRecord
from app.voice.session import (
    CallSession,
    create_session,
    end_session,
    get_session,
)

logger = logging.getLogger(__name__)


def _gather(response: VoiceResponse, message: str) -> VoiceResponse:
    """Append a <Say> + <Gather speech> to a TwiML response."""
    settings = get_settings()
    gather = Gather(
        input="speech",
        action="/voice/process-speech",
        method="POST",
        timeout=settings.gather_timeout,
        speech_timeout=settings.speech_timeout,
        language=settings.language,
    )
    gather.say(message, voice=settings.tts_voice)
    response.append(gather)
    # If no input, prompt again
    response.say(
        "I didn't catch that. Could you please repeat?",
        voice=settings.tts_voice,
    )
    response.redirect("/voice/inbound", method="POST")
    return response


async def handle_inbound_call(form_data: dict) -> str:
    """
    First webhook — a new call just arrived.
    Greet and start gathering speech.
    """
    call_sid = form_data.get("CallSid", "")
    from_number = form_data.get("From", "")
    to_number = form_data.get("To", "")

    session = create_session(
        call_sid=call_sid,
        from_number=from_number,
        to_number=to_number,
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

    response = VoiceResponse()
    return str(_gather(response, greeting))


async def handle_speech_input(form_data: dict) -> str:
    """
    Looping webhook — customer said something.
    Feed to Gemini, get response, continue or book.
    """
    call_sid = form_data.get("CallSid", "")
    speech_result = form_data.get("SpeechResult", "")
    confidence = form_data.get("Confidence", "0")

    session = get_session(call_sid)
    if not session:
        # Session lost (restart, etc.) — recreate
        session = create_session(
            call_sid=call_sid,
            from_number=form_data.get("From", ""),
            to_number=form_data.get("To", ""),
            call_type=CallType.INBOUND,
        )

    logger.info("Call %s — speech (%s confidence): %s", call_sid, confidence, speech_result)
    session.add_customer_message(speech_result)

    # Ask Gemini
    agent_response = await get_inbound_response(
        conversation_history=session.history,
        appointment=session.appointment,
    )

    # Merge extracted data
    if agent_response.extracted_data:
        session.update_appointment(agent_response.extracted_data)

    session.add_agent_message(agent_response.message)

    response = VoiceResponse()
    settings = get_settings()

    if agent_response.action == "book":
        # All info collected and confirmed — book the appointment
        return await _book_appointment(session, response, settings)

    elif agent_response.action == "end":
        response.say(agent_response.message, voice=settings.tts_voice)
        response.say(
            f"Thank you for calling {settings.salon_name}. Goodbye!",
            voice=settings.tts_voice,
        )
        response.hangup()
        await _finalize_call(session, appointment_booked=False)
        return str(response)

    elif agent_response.action == "transfer":
        response.say(agent_response.message, voice=settings.tts_voice)
        response.say(
            "Let me connect you with a team member. Please hold.",
            voice=settings.tts_voice,
        )
        # Dial the salon's real number for human hand-off
        response.dial(settings.twilio_phone_number)
        return str(response)

    else:
        # "continue" or "confirm" — keep gathering
        return str(_gather(response, agent_response.message))


async def _book_appointment(
    session: CallSession,
    response: VoiceResponse,
    settings,
) -> str:
    """Process the actual booking: Calendar + Sheets + Email."""
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

    # Send emails (non-blocking — don't fail the call if email fails)
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

    response.say(confirmation_msg, voice=settings.tts_voice)
    response.hangup()

    await _finalize_call(session, appointment_booked=True)
    return str(response)


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
    call_sid = form_data.get("CallSid", "")
    status = form_data.get("CallStatus", "")
    logger.info("Call %s status: %s", call_sid, status)

    if status in ("completed", "failed", "busy", "no-answer", "canceled"):
        session = get_session(call_sid)
        if session:
            await _finalize_call(session, appointment_booked=session.appointment_booked)
