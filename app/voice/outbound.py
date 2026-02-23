"""
Outbound call handler.

Triggers calls to customers for appointment confirmations,
reminders, and follow-ups via the Twilio REST API.
"""

from __future__ import annotations

import json
import logging

from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import Gather, VoiceResponse

from app.ai.gemini_agent import get_outbound_response
from app.config import get_settings
from app.models.appointment import CallType, OutboundCallRequest, TranscriptRecord
from app.integrations.google_sheets import log_transcript
from app.voice.session import (
    create_session,
    end_session,
    get_session,
)

logger = logging.getLogger(__name__)


def initiate_outbound_call(request: OutboundCallRequest) -> dict:
    """
    Start an outbound call using Twilio REST API.
    Returns the call SID and status.
    """
    settings = get_settings()
    client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)

    # TwiML URL — Twilio will hit this when the customer answers
    twiml_url = f"{settings.base_url}/voice/outbound-answer"
    status_url = f"{settings.base_url}/voice/status"

    call = client.calls.create(
        to=request.phone_number,
        from_=settings.twilio_phone_number,
        url=twiml_url,
        status_callback=status_url,
        status_callback_event=["completed", "failed", "busy", "no-answer"],
        # Pass context via custom parameters
        status_callback_method="POST",
    )

    # Pre-create session so context is ready when the call connects
    context = ""
    if request.appointment_details:
        context = json.dumps(request.appointment_details)
    elif request.custom_message:
        context = request.custom_message

    create_session(
        call_sid=call.sid,
        from_number=settings.twilio_phone_number,
        to_number=request.phone_number,
        call_type=CallType.OUTBOUND,
        purpose=request.purpose,
        context=context,
    )

    logger.info("Outbound call initiated: %s → %s (SID: %s)", settings.twilio_phone_number, request.phone_number, call.sid)

    return {
        "call_sid": call.sid,
        "status": call.status,
        "to": request.phone_number,
        "purpose": request.purpose,
    }


async def handle_outbound_answer(form_data: dict) -> str:
    """
    Webhook hit when the outbound call is answered.
    Deliver the opening message based on call purpose.
    """
    call_sid = form_data.get("CallSid", "")
    settings = get_settings()

    session = get_session(call_sid)
    if not session:
        # Shouldn't happen, but be safe
        response = VoiceResponse()
        response.say(
            f"Hello, this is {settings.salon_name}. We apologize, but we encountered "
            "a technical issue. We'll call you back shortly. Goodbye.",
            voice=settings.tts_voice,
        )
        response.hangup()
        return str(response)

    # Get the opening message from Gemini
    agent_response = await get_outbound_response(
        conversation_history=[],
        purpose=session.purpose,
        context=session.context,
    )

    session.add_agent_message(agent_response.message)

    response = VoiceResponse()
    gather = Gather(
        input="speech",
        action="/voice/outbound-process",
        method="POST",
        timeout=settings.gather_timeout,
        speech_timeout=settings.speech_timeout,
        language=settings.language,
    )
    gather.say(agent_response.message, voice=settings.tts_voice)
    response.append(gather)
    response.say(
        "I didn't hear a response. I'll try again.",
        voice=settings.tts_voice,
    )
    response.redirect("/voice/outbound-answer", method="POST")
    return str(response)


async def handle_outbound_speech(form_data: dict) -> str:
    """
    Process customer speech during an outbound call.
    """
    call_sid = form_data.get("CallSid", "")
    speech_result = form_data.get("SpeechResult", "")
    settings = get_settings()

    session = get_session(call_sid)
    if not session:
        response = VoiceResponse()
        response.say("Thank you. Goodbye.", voice=settings.tts_voice)
        response.hangup()
        return str(response)

    session.add_customer_message(speech_result)

    agent_response = await get_outbound_response(
        conversation_history=session.history,
        purpose=session.purpose,
        context=session.context,
    )

    if agent_response.extracted_data:
        session.update_appointment(agent_response.extracted_data)

    session.add_agent_message(agent_response.message)

    response = VoiceResponse()

    if agent_response.action == "end":
        response.say(agent_response.message, voice=settings.tts_voice)
        response.say(
            f"Thank you, and have a wonderful day! Goodbye.",
            voice=settings.tts_voice,
        )
        response.hangup()
        await _finalize_outbound(session)
        return str(response)

    # Continue conversation
    gather = Gather(
        input="speech",
        action="/voice/outbound-process",
        method="POST",
        timeout=settings.gather_timeout,
        speech_timeout=settings.speech_timeout,
        language=settings.language,
    )
    gather.say(agent_response.message, voice=settings.tts_voice)
    response.append(gather)
    response.say(
        "I didn't catch that. Could you say that again?",
        voice=settings.tts_voice,
    )
    response.redirect("/voice/outbound-answer", method="POST")
    return str(response)


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
