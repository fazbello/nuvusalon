"""
Rule-based response engine — zero AI API cost.

Handles inbound/outbound salon conversations using keyword matching,
regex data extraction, and the live knowledge base.

Covers ~80% of real salon call scenarios:
  - Full booking flow (service → date → time → name → phone → email → confirm → book)
  - Hours/location/services/pricing queries
  - FAQ lookup from knowledge base
  - Appointment confirmation and reminders (outbound)

No API keys required. Self-improves via the learner module.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from app.config import get_settings
from app.knowledge_base.loader import get_full_kb, get_services_flat
from app.models.appointment import AgentResponse, AppointmentData

logger = logging.getLogger(__name__)

# ── Intent keyword map ────────────────────────────────────────────────────────

_INTENTS: dict[str, list[str]] = {
    "book": [
        "book", "appointment", "schedule", "reserve", "come in",
        "want a", "need a", "make an appointment", "get a", "i'd like",
        "i would like", "can i get", "set up",
    ],
    "hours": [
        "hour", "open", "close", "closing", "opening", "timing",
        "when do you", "what time", "are you open",
    ],
    "services": [
        "service", "what do you do", "what do you offer", "price",
        "cost", "how much", "menu", "do you do", "do you have", "offer",
    ],
    "location": [
        "where", "address", "location", "directions", "find you",
        "located", "how do i get",
    ],
    "cancel": ["cancel", "reschedule", "change my appointment", "change appointment"],
    "confirm": [
        "yes", "yeah", "yep", "yup", "correct", "that's right",
        "sounds good", "perfect", "ok", "okay", "sure", "absolutely",
        "right", "great", "confirm", "confirmed",
    ],
    "deny": ["no", "nope", "wrong", "incorrect", "not right", "different", "change"],
    "repeat": ["sorry", "what", "repeat", "again", "didn't catch", "pardon", "come again"],
    "goodbye": [
        "thank you", "thanks", "bye", "goodbye", "that's all",
        "that is all", "nothing else", "all done", "no more",
    ],
}

# ── Booking flow prompts ─────────────────────────────────────────────────────

_PROMPTS: dict[str, str] = {
    "service":        "What service would you like today?",
    "preferred_date": "What date would you like to come in?",
    "preferred_time": "What time works best for you?",
    "customer_name":  "Can I get your full name please?",
    "phone_number":   "And what is a good phone number for you?",
    "email":          "What is your email address so we can send a confirmation?",
}

# ── Date helpers ─────────────────────────────────────────────────────────────

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
_DAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


# ── Extraction functions ─────────────────────────────────────────────────────

def _intent(text: str) -> str:
    t = text.lower()
    for intent, keywords in _INTENTS.items():
        if any(kw in t for kw in keywords):
            return intent
    return "unknown"


def _extract_service(text: str) -> str | None:
    t = text.lower()
    for svc in get_services_flat():
        if svc.lower() in t:
            return svc
    return None


def _extract_date(text: str) -> str | None:
    t = text.lower()
    today = date.today()

    if "today" in t:
        return today.isoformat()
    if "tomorrow" in t:
        return (today + timedelta(days=1)).isoformat()

    # Named day: "monday", "next friday"
    for day_name, day_num in _DAYS.items():
        if day_name in t:
            ahead = (day_num - today.weekday()) % 7 or 7
            return (today + timedelta(days=ahead)).isoformat()

    # "March 15", "15th of March", "the 15th"
    for month_name, month_num in _MONTHS.items():
        if month_name in t:
            m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?", t)
            if m:
                try:
                    year = today.year
                    d = date(year, month_num, int(m.group(1)))
                    if d < today:
                        d = date(year + 1, month_num, int(m.group(1)))
                    return d.isoformat()
                except ValueError:
                    pass

    # MM/DD or DD/MM with optional year
    m = re.search(r"\b(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?\b", text)
    if m:
        try:
            a, b = int(m.group(1)), int(m.group(2))
            year = int(m.group(3)) if m.group(3) else today.year
            if year < 100:
                year += 2000
            d = date(year, a, b)  # assume MM/DD (US)
            if d < today:
                d = date(year + 1, a, b)
            return d.isoformat()
        except ValueError:
            pass

    return None


def _extract_time(text: str) -> str | None:
    t = text.lower()

    if "noon" in t or "midday" in t:
        return "12:00"
    if "midnight" in t:
        return "00:00"

    # "2:30 pm", "14:30"
    m = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", t)
    if m:
        h, mn, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return f"{h:02d}:{mn:02d}"

    # "2pm", "2 pm", "two pm"
    m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", t)
    if m:
        h, ampm = int(m.group(1)), m.group(2)
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return f"{h:02d}:00"

    return None


def _extract_name(text: str) -> str | None:
    m = re.search(
        r"(?:my name is|i'?m|i am|it'?s|name'?s|call me)\s+([A-Z][a-z]+"
        r"(?:\s+[A-Z][a-z]+)?)",
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()

    # Whole response is a short name
    words = text.strip().split()
    if 1 <= len(words) <= 3 and all(re.match(r"[A-Za-z\-']+$", w) for w in words):
        return text.strip().title()

    return None


def _extract_phone(text: str) -> str | None:
    digits = re.sub(r"[^\d+]", "", text)
    if len(digits) >= 10:
        return digits
    return None


def _extract_email(text: str) -> str | None:
    m = re.search(r"\b[\w._%+\-]+@[\w.\-]+\.[a-zA-Z]{2,}\b", text)
    return m.group() if m else None


def _extract_all(text: str, appt: AppointmentData) -> dict:
    """Pull as many fields as possible from a single speech turn."""
    out: dict = {}
    if not appt.service:
        v = _extract_service(text)
        if v:
            out["service"] = v
    if not appt.preferred_date:
        v = _extract_date(text)
        if v:
            out["preferred_date"] = v
    if not appt.preferred_time:
        v = _extract_time(text)
        if v:
            out["preferred_time"] = v
    if not appt.customer_name:
        v = _extract_name(text)
        if v:
            out["customer_name"] = v
    if not appt.phone_number:
        v = _extract_phone(text)
        if v:
            out["phone_number"] = v
    if not appt.email:
        v = _extract_email(text)
        if v:
            out["email"] = v
    return out


# ── Knowledge base helpers ───────────────────────────────────────────────────

def _hours_message() -> str:
    locs = get_full_kb().get("locations", [])
    if not locs:
        return "I don't have our current hours on hand. Please check our website or give us a call."
    parts = []
    for loc in locs[:2]:
        name = loc.get("name", "")
        hours = loc.get("hours", {})
        if hours:
            snippet = ", ".join(
                f"{d.title()}: {t}" for d, t in list(hours.items())[:3]
            )
            parts.append(f"{name + ': ' if name else ''}{snippet}…")
    return "Our hours are: " + "; ".join(parts) + " Would you like to book an appointment?"


def _services_message() -> str:
    svcs = get_services_flat()
    if not svcs:
        return "We offer a range of hair and beauty services. What are you looking for?"
    shown = svcs[:8]
    extra = len(svcs) - 8
    tail = f", and {extra} more" if extra > 0 else ""
    return f"We offer: {', '.join(shown)}{tail}. Which service would you like to book?"


def _location_message() -> str:
    locs = get_full_kb().get("locations", [])
    if not locs:
        return "Please check our website for our address. Can I help you book an appointment?"
    parts = [
        (f"{l.get('name', '')}: " if l.get("name") else "") + l.get("address", "")
        for l in locs[:2]
        if l.get("address")
    ]
    return (
        ("We are located at: " + "; ".join(parts) + ". " if parts else "")
        + "Would you like to book an appointment?"
    )


def _faq_lookup(text: str) -> str | None:
    faq = get_full_kb().get("faq", [])
    t_words = set(text.lower().split())
    for item in faq:
        q_words = set(
            w for w in item.get("question", "").lower().split() if len(w) > 3
        )
        if q_words and len(q_words & t_words) >= min(2, len(q_words)):
            return item.get("answer", "")
    return None


def _services_with_prices() -> str:
    """Return a price summary for common 'how much' queries."""
    kb = get_full_kb()
    lines = []
    for cat in kb.get("services", []):
        for item in cat.get("items", [])[:3]:
            name = item.get("name", "")
            price = item.get("price", "")
            if name and price:
                lines.append(f"{name}: {price}")
    if not lines:
        return "Please contact us for pricing details."
    shown = lines[:6]
    return "Our prices: " + ", ".join(shown) + (". And more — ask us for the full menu!" if len(lines) > 6 else ".")


# ── Last-turn context helper ─────────────────────────────────────────────────

def _last_agent_msg(history: list[dict]) -> str:
    for turn in reversed(history):
        if turn.get("role") == "agent":
            return turn.get("content", "").lower()
    return ""


# ── Main inbound response ────────────────────────────────────────────────────

def get_rule_based_inbound_response(
    speech: str,
    appointment: AppointmentData,
    history: list[dict],
) -> AgentResponse:
    """
    Produce an inbound response from rules only — no API call.
    Compatible return type: AgentResponse.
    """
    settings = get_settings()
    name = settings.salon_name
    intent = _intent(speech)
    extracted = _extract_all(speech, appointment)

    # Working copy with newly extracted data merged in
    merged = appointment.model_copy(update=extracted)
    last_msg = _last_agent_msg(history)

    # ── Goodbye ──────────────────────────────────────────────────────────────
    if intent == "goodbye" and not merged.service:
        return AgentResponse(
            message=f"Thank you for calling {name}! Have a wonderful day.",
            extracted_data=extracted,
            action="end",
        )

    # ── Repeat request ───────────────────────────────────────────────────────
    if intent == "repeat":
        if last_msg:
            return AgentResponse(message=last_msg, extracted_data=extracted, action="continue")

    # ── Informational queries (only if we haven't started booking yet) ───────
    if not merged.service:
        if intent == "hours":
            return AgentResponse(message=_hours_message(), extracted_data=extracted, action="continue")
        if intent == "location":
            return AgentResponse(message=_location_message(), extracted_data=extracted, action="continue")
        if intent == "services":
            # Pricing sub-query
            if any(w in speech.lower() for w in ("price", "cost", "how much", "charge")):
                return AgentResponse(message=_services_with_prices(), extracted_data=extracted, action="continue")
            return AgentResponse(message=_services_message(), extracted_data=extracted, action="continue")

        # FAQ lookup
        if intent == "unknown":
            answer = _faq_lookup(speech)
            if answer:
                return AgentResponse(
                    message=f"{answer} Is there anything else I can help with, or would you like to book an appointment?",
                    extracted_data=extracted,
                    action="continue",
                )

            # Log for admin review (learner will handle this)
            try:
                from app.ai.learner import log_unknown_phrase
                log_unknown_phrase(speech)
            except Exception:
                pass

    # ── Confirmation / denial of booking summary ──────────────────────────────
    if "is that correct" in last_msg or "confirm your" in last_msg or "does that sound" in last_msg:
        if intent == "confirm":
            return AgentResponse(
                message="Perfect! I am booking your appointment now.",
                extracted_data=extracted,
                action="book",
            )
        if intent == "deny":
            return AgentResponse(
                message="No problem! Let me start over. What service would you like to book today?",
                extracted_data={k: None for k in AppointmentData.model_fields},  # clear all
                action="continue",
            )

    # ── Booking flow ─────────────────────────────────────────────────────────
    missing = merged.missing_required_fields()

    if not missing:
        # All fields collected — ask for confirmation
        d = merged.preferred_date or "?"
        t = merged.preferred_time or "?"
        svc = merged.service or "?"
        n = merged.customer_name or "?"
        return AgentResponse(
            message=(
                f"Let me confirm: a {svc} appointment on {d} at {t} "
                f"for {n}. Does that sound right?"
            ),
            extracted_data=extracted,
            action="confirm",
        )

    # Ask for the next missing field
    next_field = missing[0]
    prompt = _PROMPTS.get(next_field, f"Can I get your {next_field.replace('_', ' ')}?")

    # Enrich service prompt with options
    if next_field == "service":
        svcs = get_services_flat()
        if svcs:
            sample = ", ".join(svcs[:5])
            extra = f" and more" if len(svcs) > 5 else ""
            prompt = f"What service would you like? We offer {sample}{extra}."

    return AgentResponse(message=prompt, extracted_data=extracted, action="continue")


# ── Outbound rule-based response ─────────────────────────────────────────────

def get_rule_based_outbound_response(
    conversation_history: list[dict],
    purpose: str,
    context: str,
) -> AgentResponse:
    """
    Rule-based outbound call handler for confirmations and reminders.
    Uses templates populated from context JSON.
    """
    import json as _json
    settings = get_settings()
    name = settings.salon_name

    try:
        ctx = _json.loads(context) if context.startswith("{") else {}
    except Exception:
        ctx = {}

    customer = ctx.get("customer_name", "")
    service = ctx.get("service", "your appointment")
    appt_date = ctx.get("date", "")
    appt_time = ctx.get("time", "")

    greeting = f"Hello{', ' + customer if customer else ''}! This is {name} calling."
    last_agent = _last_agent_msg(conversation_history)
    intent = _intent(conversation_history[-1]["content"] if conversation_history else "")

    # First turn — deliver the main message
    if not conversation_history or not last_agent:
        if purpose in ("appointment_confirmation", "confirmation"):
            msg = (
                f"{greeting} I'm calling to confirm your {service} appointment"
                + (f" on {appt_date}" if appt_date else "")
                + (f" at {appt_time}" if appt_time else "")
                + ". Can you confirm you're still coming in?"
            )
        elif purpose in ("reminder",):
            msg = (
                f"{greeting} Just a friendly reminder about your {service} appointment"
                + (f" on {appt_date}" if appt_date else "")
                + (f" at {appt_time}" if appt_time else "")
                + ". We look forward to seeing you!"
            )
        else:
            msg = f"{greeting} {context or 'Is there anything we can help you with?'}"
        return AgentResponse(message=msg, extracted_data={}, action="continue")

    # Follow-up turns
    if intent == "confirm":
        return AgentResponse(
            message=f"Wonderful! We have you confirmed. See you then. Have a great day!",
            extracted_data={},
            action="end",
        )
    if intent == "deny" or "cancel" in conversation_history[-1].get("content", "").lower():
        return AgentResponse(
            message=(
                f"I understand. I'll make a note. Please call us at {settings.twilio_phone_number or 'our salon number'} "
                "to reschedule. Have a wonderful day!"
            ),
            extracted_data={},
            action="end",
        )
    if intent == "goodbye":
        return AgentResponse(
            message="Thank you! Have a wonderful day. Goodbye!",
            extracted_data={},
            action="end",
        )

    # Generic follow-up
    return AgentResponse(
        message="Is there anything else I can help you with?",
        extracted_data={},
        action="continue",
    )
