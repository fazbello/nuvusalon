"""
OpenAI-powered conversation agent.

Drop-in alternative to gemini_agent.py — same interface, different backend.
Activated by setting AI_PROVIDER=openai in env vars or Configure > Voice & AI.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date

from openai import AsyncOpenAI

from app.ai.prompts import (
    OUTBOUND_SYSTEM_PROMPT,
    RECEPTIONIST_SYSTEM_PROMPT,
    RESEARCH_PROMPT,
)
from app.config import get_settings
from app.knowledge_base.loader import get_kb_summary_for_agent
from app.models.appointment import AgentResponse, AppointmentData

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=get_settings().openai_api_key)
    return _client


def reset_client() -> None:
    """Force a new client to be created on the next call (e.g. after API key change)."""
    global _client
    _client = None


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
    """Parse JSON from the model, tolerating markdown fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)
    try:
        data = json.loads(cleaned)
        return AgentResponse(**data)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("Failed to parse OpenAI agent JSON: %s — raw: %s", exc, text[:200])
        return AgentResponse(
            message=text[:500],
            extracted_data={},
            action="continue",
        )


def _build_messages(system_prompt: str, conversation_history: list[dict], last_msg: str) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for msg in conversation_history[:-1]:  # history except the last message
        role = "user" if msg["role"] == "customer" else "assistant"
        messages.append({"role": role, "content": msg["content"]})
    messages.append({"role": "user", "content": last_msg})
    return messages


async def get_inbound_response(
    conversation_history: list[dict],
    appointment: AppointmentData,
) -> AgentResponse:
    """Return the next agent message for an inbound call conversation."""
    client = _get_client()
    settings = get_settings()
    system_prompt = _build_inbound_system_prompt(appointment)

    last_msg = conversation_history[-1]["content"] if conversation_history else "The customer just called."
    messages = _build_messages(system_prompt, conversation_history, last_msg)

    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        temperature=0.7,
        max_tokens=300,
    )
    return _parse_agent_response(response.choices[0].message.content or "")


async def get_outbound_response(
    conversation_history: list[dict],
    purpose: str,
    context: str,
) -> AgentResponse:
    """Return the next agent message for an outbound call conversation."""
    client = _get_client()
    settings = get_settings()
    system_prompt = _build_outbound_system_prompt(purpose, context)

    last_msg = conversation_history[-1]["content"] if conversation_history else "The customer just answered the phone."
    messages = _build_messages(system_prompt, conversation_history, last_msg)

    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        temperature=0.7,
        max_tokens=300,
    )
    return _parse_agent_response(response.choices[0].message.content or "")


async def research(question: str) -> str:
    """Use OpenAI for general salon/spa industry research."""
    client = _get_client()
    settings = get_settings()

    prompt = RESEARCH_PROMPT.format(
        salon_name=settings.salon_name,
        question=question,
    )

    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=1024,
    )
    return response.choices[0].message.content or ""
