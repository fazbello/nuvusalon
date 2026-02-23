"""
Gemini-powered conversation agent.

Handles both inbound and outbound call conversations,
plus a standalone research endpoint.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import google.generativeai as genai

from app.ai.prompts import (
    OUTBOUND_SYSTEM_PROMPT,
    RECEPTIONIST_SYSTEM_PROMPT,
    RESEARCH_PROMPT,
)
from app.config import get_settings
from app.knowledge_base.loader import get_kb_summary_for_agent
from app.models.appointment import AgentResponse, AppointmentData

logger = logging.getLogger(__name__)

_configured = False


def _ensure_configured() -> None:
    global _configured
    if not _configured:
        genai.configure(api_key=get_settings().gemini_api_key)
        _configured = True


def _build_inbound_system_prompt(appointment: AppointmentData) -> str:
    settings = get_settings()
    return RECEPTIONIST_SYSTEM_PROMPT.format(
        salon_name=settings.salon_name,
        today=date.today().isoformat(),
        knowledge_base=get_kb_summary_for_agent(),
        collected_data=appointment.summary(),
    )


def _build_outbound_system_prompt(purpose: str, context: str) -> str:
    settings = get_settings()
    return OUTBOUND_SYSTEM_PROMPT.format(
        salon_name=settings.salon_name,
        purpose=purpose,
        context=context,
        knowledge_base=get_kb_summary_for_agent(),
    )


def _parse_agent_response(text: str) -> AgentResponse:
    """Parse JSON from Gemini, tolerating markdown fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Strip ```json ... ```
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)
    try:
        data = json.loads(cleaned)
        return AgentResponse(**data)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("Failed to parse agent JSON: %s — raw: %s", exc, text[:200])
        # Graceful fallback: treat entire text as the spoken message
        return AgentResponse(
            message=text[:500],
            extracted_data={},
            action="continue",
        )


async def get_inbound_response(
    conversation_history: list[dict],
    appointment: AppointmentData,
) -> AgentResponse:
    """
    Given the conversation so far and currently-collected data,
    return the next thing to say plus any extracted fields.
    """
    _ensure_configured()
    settings = get_settings()

    system_prompt = _build_inbound_system_prompt(appointment)

    model = genai.GenerativeModel(
        model_name=settings.gemini_model,
        system_instruction=system_prompt,
        generation_config=genai.GenerationConfig(
            temperature=0.7,
            max_output_tokens=300,
        ),
    )

    # Build Gemini-format history
    gemini_history = []
    for msg in conversation_history:
        role = "user" if msg["role"] == "customer" else "model"
        gemini_history.append({"role": role, "parts": [msg["content"]]})

    chat = model.start_chat(history=gemini_history[:-1] if gemini_history else [])

    # Send the latest customer message (or an init prompt)
    last_msg = gemini_history[-1]["parts"][0] if gemini_history else "The customer just called."
    response = chat.send_message(last_msg)

    return _parse_agent_response(response.text)


async def get_outbound_response(
    conversation_history: list[dict],
    purpose: str,
    context: str,
) -> AgentResponse:
    """Handle outbound call conversation turns."""
    _ensure_configured()
    settings = get_settings()

    system_prompt = _build_outbound_system_prompt(purpose, context)

    model = genai.GenerativeModel(
        model_name=settings.gemini_model,
        system_instruction=system_prompt,
        generation_config=genai.GenerationConfig(
            temperature=0.7,
            max_output_tokens=300,
        ),
    )

    gemini_history = []
    for msg in conversation_history:
        role = "user" if msg["role"] == "customer" else "model"
        gemini_history.append({"role": role, "parts": [msg["content"]]})

    chat = model.start_chat(history=gemini_history[:-1] if gemini_history else [])
    last_msg = (
        gemini_history[-1]["parts"][0]
        if gemini_history
        else "The customer just answered the phone."
    )
    response = chat.send_message(last_msg)

    return _parse_agent_response(response.text)


async def research(question: str) -> str:
    """
    Use Gemini for general salon/spa industry research.
    Returns a plain-text answer.
    """
    _ensure_configured()
    settings = get_settings()

    prompt = RESEARCH_PROMPT.format(
        salon_name=settings.salon_name,
        question=question,
    )

    model = genai.GenerativeModel(
        model_name=settings.gemini_model,
        generation_config=genai.GenerationConfig(
            temperature=0.4,
            max_output_tokens=1024,
        ),
    )
    response = model.generate_content(prompt)
    return response.text
