"""
Gemini-powered conversation agent.

Handles both inbound and outbound call conversations,
plus a standalone research endpoint.

Uses the current `google-genai` SDK (not the deprecated `google-generativeai`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date

from google import genai
from google.genai import types

from app.ai.prompts import (
    OUTBOUND_SYSTEM_PROMPT,
    RECEPTIONIST_SYSTEM_PROMPT,
    RESEARCH_PROMPT,
)
from app.config import get_settings
from app.knowledge_base.loader import get_kb_summary_for_agent
from app.models.appointment import AgentResponse, AppointmentData

logger = logging.getLogger(__name__)

_client: genai.Client | None = None

# Models known to work with the API; used to validate configured model names.
_FALLBACK_MODEL = "gemini-2.0-flash"
_KNOWN_MODELS: frozenset[str] = frozenset({
    "gemini-2.0-flash",
    "gemini-2.0-flash-exp",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-1.5-pro",
})


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=get_settings().gemini_api_key)
    return _client


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
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)
    try:
        data = json.loads(cleaned)
        return AgentResponse(**data)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("Failed to parse agent JSON: %s — raw: %s", exc, text[:200])
        return AgentResponse(
            message=text[:500],
            extracted_data={},
            action="continue",
        )


def _build_chat_history(
    conversation_history: list[dict],
) -> tuple[list[types.Content], str]:
    """
    Convert our conversation history into google-genai Content objects.
    Returns (history_for_chat, last_message_text).
    """
    contents: list[types.Content] = []
    for msg in conversation_history:
        role = "user" if msg["role"] == "customer" else "model"
        contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))

    if contents:
        last_msg = conversation_history[-1]["content"]
        history = contents[:-1]
    else:
        last_msg = ""
        history = []

    return history, last_msg


def _sync_inbound(conversation_history: list[dict], appointment: AppointmentData) -> AgentResponse:
    """Synchronous Gemini call for inbound conversations (runs in thread pool)."""
    client = _get_client()
    settings = get_settings()
    system_prompt = _build_inbound_system_prompt(appointment)

    history, last_msg = _build_chat_history(conversation_history)
    if not last_msg:
        last_msg = "The customer just called."

    model = settings.gemini_model
    if model not in _KNOWN_MODELS:
        logger.warning(
            "Configured gemini_model %r is not a known model — falling back to %s. "
            "Fix: set gemini_model to a valid model in Configure > Voice & AI.",
            model, _FALLBACK_MODEL,
        )
        model = _FALLBACK_MODEL

    try:
        chat = client.chats.create(
            model=model,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.7,
                max_output_tokens=300,
            ),
            history=history,
        )
        response = chat.send_message(last_msg)
    except Exception as exc:
        exc_str = str(exc)
        if ("404" in exc_str or "not found" in exc_str.lower()) and model != _FALLBACK_MODEL:
            logger.warning(
                "Gemini model %r not found (404) — retrying with %s. Error: %s",
                model, _FALLBACK_MODEL, exc,
            )
            chat = client.chats.create(
                model=_FALLBACK_MODEL,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.7,
                    max_output_tokens=300,
                ),
                history=history,
            )
            response = chat.send_message(last_msg)
        else:
            raise

    return _parse_agent_response(response.text)


async def get_inbound_response(
    conversation_history: list[dict],
    appointment: AppointmentData,
) -> AgentResponse:
    """
    Given the conversation so far and currently-collected data,
    return the next thing to say plus any extracted fields.
    """
    return await asyncio.to_thread(_sync_inbound, conversation_history, appointment)


def _sync_outbound(conversation_history: list[dict], purpose: str, context: str) -> AgentResponse:
    """Synchronous Gemini call for outbound conversations (runs in thread pool)."""
    client = _get_client()
    settings = get_settings()
    system_prompt = _build_outbound_system_prompt(purpose, context)

    history, last_msg = _build_chat_history(conversation_history)
    if not last_msg:
        last_msg = "The customer just answered the phone."

    model = settings.gemini_model
    if model not in _KNOWN_MODELS:
        logger.warning(
            "Configured gemini_model %r is not a known model — falling back to %s. "
            "Fix: set gemini_model to a valid model in Configure > Voice & AI.",
            model, _FALLBACK_MODEL,
        )
        model = _FALLBACK_MODEL

    try:
        chat = client.chats.create(
            model=model,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.7,
                max_output_tokens=300,
            ),
            history=history,
        )
        response = chat.send_message(last_msg)
    except Exception as exc:
        exc_str = str(exc)
        if ("404" in exc_str or "not found" in exc_str.lower()) and model != _FALLBACK_MODEL:
            logger.warning(
                "Gemini model %r not found (404) — retrying with %s. Error: %s",
                model, _FALLBACK_MODEL, exc,
            )
            chat = client.chats.create(
                model=_FALLBACK_MODEL,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.7,
                    max_output_tokens=300,
                ),
                history=history,
            )
            response = chat.send_message(last_msg)
        else:
            raise

    return _parse_agent_response(response.text)


async def get_outbound_response(
    conversation_history: list[dict],
    purpose: str,
    context: str,
) -> AgentResponse:
    """Handle outbound call conversation turns."""
    return await asyncio.to_thread(_sync_outbound, conversation_history, purpose, context)


async def research(question: str) -> str:
    """
    Use Gemini for general salon/spa industry research.
    Returns a plain-text answer.
    """
    client = _get_client()
    settings = get_settings()

    prompt = RESEARCH_PROMPT.format(
        salon_name=settings.salon_name,
        question=question,
    )

    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.4,
            max_output_tokens=1024,
        ),
    )
    return response.text
