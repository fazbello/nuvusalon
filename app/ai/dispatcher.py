"""
AI provider dispatcher.

Routes inbound/outbound conversations to the configured backend:
  - "rules"  → app/ai/rule_engine.py  (zero API cost, always available)
  - "gemini" → app/ai/gemini_agent.py (Google Gemini)
  - "openai" → app/ai/openai_agent.py (OpenAI ChatGPT)

Failure resilience:
  If an AI provider (Gemini/OpenAI) raises an exception, the dispatcher
  automatically falls back to the rule engine so callers always get a
  response, even during API outages or when credits run out.
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

    if provider == "rules":
        return _rules_inbound(conversation_history, appointment)

    # AI providers — with automatic fallback to rules on failure
    try:
        if provider == "openai":
            from app.ai.openai_agent import get_inbound_response as fn
        else:
            if provider != "gemini":
                logger.warning("Unknown ai_provider %r — using gemini", provider)
            from app.ai.gemini_agent import get_inbound_response as fn
        return await fn(conversation_history, appointment)
    except Exception as exc:
        logger.warning(
            "AI provider %r failed (%s) — falling back to rule engine", provider, exc
        )
        return _rules_inbound(conversation_history, appointment)


async def get_outbound_response(
    conversation_history: list[dict],
    purpose: str,
    context: str,
) -> AgentResponse:
    provider = _provider()

    if provider == "rules":
        return _rules_outbound(conversation_history, purpose, context)

    try:
        if provider == "openai":
            from app.ai.openai_agent import get_outbound_response as fn
        else:
            if provider != "gemini":
                logger.warning("Unknown ai_provider %r — using gemini", provider)
            from app.ai.gemini_agent import get_outbound_response as fn
        return await fn(conversation_history, purpose, context)
    except Exception as exc:
        logger.warning(
            "AI provider %r failed (%s) — falling back to rule engine", provider, exc
        )
        return _rules_outbound(conversation_history, purpose, context)


async def research(question: str) -> str:
    """Research only works with AI providers; rule engine returns a placeholder."""
    provider = _provider()
    if provider == "rules":
        return (
            "Research requires an AI provider (Gemini or OpenAI). "
            "Switch AI Provider in Configure > Voice & AI to use this feature."
        )
    try:
        if provider == "openai":
            from app.ai.openai_agent import research as fn
        else:
            from app.ai.gemini_agent import research as fn
        return await fn(question)
    except Exception as exc:
        logger.warning("AI research failed: %s", exc)
        return f"Research unavailable: {exc}"


# ── Rule engine wrappers ──────────────────────────────────────────────────────

def _rules_inbound(
    conversation_history: list[dict],
    appointment: AppointmentData,
) -> AgentResponse:
    from app.ai.rule_engine import get_rule_based_inbound_response
    speech = ""
    for turn in reversed(conversation_history):
        if turn.get("role") == "customer":
            speech = turn.get("content", "")
            break
    return get_rule_based_inbound_response(speech, appointment, conversation_history)


def _rules_outbound(
    conversation_history: list[dict],
    purpose: str,
    context: str,
) -> AgentResponse:
    from app.ai.rule_engine import get_rule_based_outbound_response
    return get_rule_based_outbound_response(conversation_history, purpose, context)
