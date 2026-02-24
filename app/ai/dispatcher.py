"""
AI provider dispatcher.

Routes inbound/outbound call conversations to the active AI backend
(Gemini or OpenAI) based on the AI_PROVIDER setting.

Usage:
    from app.ai.dispatcher import get_inbound_response, get_outbound_response
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.models.appointment import AgentResponse, AppointmentData

logger = logging.getLogger(__name__)


def _provider() -> str:
    return get_settings().ai_provider.lower()


async def get_inbound_response(
    conversation_history: list[dict],
    appointment: AppointmentData,
) -> AgentResponse:
    provider = _provider()
    if provider == "openai":
        from app.ai.openai_agent import get_inbound_response as fn
    else:
        if provider != "gemini":
            logger.warning("Unknown ai_provider %r — falling back to gemini", provider)
        from app.ai.gemini_agent import get_inbound_response as fn
    return await fn(conversation_history, appointment)


async def get_outbound_response(
    conversation_history: list[dict],
    purpose: str,
    context: str,
) -> AgentResponse:
    provider = _provider()
    if provider == "openai":
        from app.ai.openai_agent import get_outbound_response as fn
    else:
        if provider != "gemini":
            logger.warning("Unknown ai_provider %r — falling back to gemini", provider)
        from app.ai.gemini_agent import get_outbound_response as fn
    return await fn(conversation_history, purpose, context)


async def research(question: str) -> str:
    provider = _provider()
    if provider == "openai":
        from app.ai.openai_agent import research as fn
    else:
        from app.ai.gemini_agent import research as fn
    return await fn(question)
