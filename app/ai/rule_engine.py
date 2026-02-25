"""
Rule-based response engine — zero AI API cost.

Handles inbound/outbound salon conversations using keyword matching,
regex data extraction, and the live knowledge base.

Covers ~80% of real salon call scenarios:
  - Full booking flow (service → date → time → name → email [optional] → confirm → book)
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
    "skip": ["skip", "no email", "don't have", "don't want", "no thanks", "that's fine", "pass"],
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
}

# ── Word-to-number maps ───────────────────────────────────────────────────────

_WORD_TO_NUM: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12,
}

# Spoken minute values — "two thirty", "three fifteen", etc.
_WORD_MINS_MAP: dict[str, int] = {
    "oh": 0, "zero": 0,
    "five": 5, "ten": 10,
    "fifteen": 15, "quarter": 15,
    "twenty": 20, "twenty-five": 25,
    "thirty": 30, "half": 30,
    "thirty-five": 35,
    "forty": 40, "forty-five": 45,
    "fifty": 50, "fifty-five": 55,
}

# Words that must NOT appear in a short "name" response
_NAME_EXCLUSIONS: frozenset[str] = frozenset({
    # Fillers / discourse markers
    "um", "uh", "er", "ah", "hmm", "well", "so",
    "yes", "no", "yeah", "yep", "yup", "nope",
    "sure", "right", "ok", "okay", "great", "perfect", "alright",
    # Number words (customer may be answering a time/date question)
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty",
    "hundred", "thousand",
    # Ordinals
    "first", "second", "third", "fourth", "fifth", "sixth",
    # Time / AM-PM words
    "am", "pm", "morning", "afternoon", "evening", "night", "noon", "midnight",
    "half", "quarter", "past", "oclock",
    # Day names
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    # Month names (April, May, June, August are common first names — keep them)
    "january", "february", "march", "july",
    "september", "october", "november", "december",
    # Booking / filler words
    "today", "tomorrow", "next", "the", "and", "or",
    "book", "appointment", "service", "please", "want",
})

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


# ── Display helpers ───────────────────────────────────────────────────────────

def _fmt_date(date_str: str) -> str:
    """Format ISO date to human-readable: 'Friday, March 1'."""
    try:
        d = date.fromisoformat(date_str)
        return d.strftime("%A, %B %-d")
    except Exception:
        return date_str


def _fmt_time(time_str: str) -> str:
    """Format HH:MM (24-hour) to '2:00 PM'."""
    try:
        h, m = map(int, time_str.split(":"))
        ampm = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return f"{h12}:{m:02d} {ampm}"
    except Exception:
        return time_str


# ── Extraction functions ─────────────────────────────────────────────────────

def _intent(text: str) -> str:
    t = text.lower()
    for intent, keywords in _INTENTS.items():
        if any(kw in t for kw in keywords):
            return intent
    return "unknown"


def _extract_service(text: str) -> str | None:
    """
    Match a service name from customer speech using three passes:

    1. Exact substring — "haircut & style" in text (most precise).
    2. Service-word scoring — count how many words from the service name
       appear in the customer text ("haircut" in "I want a haircut").
    3. Prefix / subword matching — a customer word is a prefix of a service
       word ("hair" → "haircut", "pedi" → "pedicure", "faci" → "facial").
       Scores 0.5 per match so exact word hits always outrank prefix hits.

    Best-scoring service wins; ties favour the more specific (longer) name.
    """
    t = text.lower()
    t_words = [w for w in re.split(r"\s+", t) if len(w) >= 3]
    _stop = {"min", "the", "and", "for", "per", "with"}

    # Pass 1 — full name substring
    for svc in get_services_flat():
        svc_name = svc["name"] if isinstance(svc, dict) else svc
        if svc_name.lower() in t:
            return svc_name

    best_name: str | None = None
    best_score: float = 0.0

    for svc in get_services_flat():
        svc_name = svc["name"] if isinstance(svc, dict) else svc
        svc_words = [
            w for w in re.split(r"[\s&,/()\-]+", svc_name.lower())
            if len(w) >= 3 and w not in _stop
        ]
        if not svc_words:
            continue

        # Pass 2 — exact service-word appears in customer text
        score: float = sum(1.0 for w in svc_words if w in t)

        # Pass 3 — customer word is a prefix of a service word
        if score == 0:
            score += sum(
                0.5
                for tw in t_words
                for sw in svc_words
                if sw.startswith(tw) and tw != sw
            )

        if score > best_score or (
            score == best_score and score > 0 and len(svc_name) > len(best_name or "")
        ):
            best_score = score
            best_name = svc_name

    return best_name if best_score >= 0.5 else None


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


def _apply_ampm(h: int, ampm: str | None, context: str = "") -> int:
    """Apply am/pm to a 12-hour value, inferring from context words when needed."""
    if ampm is None:
        if any(w in context for w in ("afternoon", "evening", "tonight", "night")):
            ampm = "pm"
        elif "morning" in context:
            ampm = "am"
        else:
            # Business-hours heuristic: 1–8 → PM (salon is open afternoons)
            if 1 <= h <= 8:
                ampm = "pm"
    if ampm == "pm" and h < 12:
        h += 12
    elif ampm == "am" and h == 12:
        h = 0
    return h


def _extract_time(text: str) -> str | None:
    """
    Extract time from speech.  Handles:
    - Digit formats:   "2:30 pm", "14:30", "2pm"
    - Phrase formats:  "half past two", "quarter past three", "quarter to four"
    - Word numbers:    "two pm", "two o'clock", "two thirty", "three fifteen"
    - Context clues:   "three in the afternoon" → 15:00
    """
    t = text.lower()

    if re.search(r"\b(noon|midday)\b", t):
        return "12:00"
    if re.search(r"\bmidnight\b", t):
        return "00:00"

    # Detect explicit am/pm once for reuse
    ampm_m = re.search(r"\b(am|pm)\b", t)
    ampm = ampm_m.group(1) if ampm_m else None

    # "2:30 pm", "14:30", "2:30"
    m = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", t)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        h = _apply_ampm(h, m.group(3) or ampm, t)
        return f"{h:02d}:{mn:02d}"

    # "2pm", "2 pm"
    m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", t)
    if m:
        h = _apply_ampm(int(m.group(1)), m.group(2), t)
        return f"{h:02d}:00"

    # "half past two", "quarter past three"
    m = re.search(r"\b(half|quarter)\s+past\s+(\w+)", t)
    if m:
        mins_word, hour_word = m.group(1), m.group(2)
        if hour_word in _WORD_TO_NUM:
            h = _apply_ampm(_WORD_TO_NUM[hour_word], ampm, t)
            mn = _WORD_MINS_MAP.get(mins_word, 0)
            return f"{h:02d}:{mn:02d}"

    # "quarter to four" → 3:45
    m = re.search(r"\bquarter\s+to\s+(\w+)", t)
    if m and m.group(1) in _WORD_TO_NUM:
        h = _apply_ampm(_WORD_TO_NUM[m.group(1)], ampm, t)
        total = h * 60 - 15
        return f"{total // 60:02d}:{total % 60:02d}"

    # Word hour + optional word minutes: "two thirty", "three fifteen", "ten thirty pm"
    # Normalise hyphens so "forty-five" stays as one token when we split
    normalised = re.sub(r"(\w)-(\w)", r"\1_\2", t)
    words = [w.replace("_", "-") for w in normalised.split()]

    for i, word in enumerate(words):
        if word not in _WORD_TO_NUM:
            continue
        h_raw = _WORD_TO_NUM[word]
        mn = 0
        found_minutes = False

        if i + 1 < len(words):
            nw = words[i + 1]
            if nw in _WORD_MINS_MAP:
                mn = _WORD_MINS_MAP[nw]
                found_minutes = True
            elif nw == "twenty" and i + 2 < len(words):
                # "twenty five", "twenty one", etc.
                _ones = {
                    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                    "six": 6, "seven": 7, "eight": 8, "nine": 9,
                }
                if words[i + 2] in _ones:
                    mn = 20 + _ones[words[i + 2]]
                else:
                    mn = 20
                found_minutes = True

        if found_minutes:
            h = _apply_ampm(h_raw, ampm, t)
            return f"{h:02d}:{mn:02d}"

        # No minutes word — return if we have any explicit time context
        has_time_context = (
            ampm is not None
            or "o'clock" in t or "oclock" in t
            or any(w in t for w in ("afternoon", "morning", "evening", "night", "tonight"))
        )
        if has_time_context:
            h = _apply_ampm(h_raw, ampm, t)
            return f"{h:02d}:00"

    # Bare digit with a preposition: "at 3", "around 2"
    m = re.search(r"\b(?:at|around|about)\s+(\d{1,2})\b", t)
    if m:
        h = _apply_ampm(int(m.group(1)), ampm, t)
        return f"{h:02d}:00"

    return None


def _extract_name(text: str) -> str | None:
    # Trigger phrases: "my name is X", "I'm X", "call me X", "the name is X"
    m = re.search(
        r"(?:my(?:\s+full)?\s+name\s+is\s+|the\s+name\s+is\s+|i'?m\s+|i\s+am\s+"
        r"|it'?s\s+|name'?s\s+|call\s+me\s+)"
        r"([A-Za-z][A-Za-z\-']*(?:\s+[A-Za-z][A-Za-z\-']*){0,2})",
        text,
        re.IGNORECASE,
    )
    if m:
        candidate = re.sub(r"[^A-Za-z\-' ]+$", "", m.group(1)).strip()
        cwords = candidate.lower().split()
        if cwords and not any(w in _NAME_EXCLUSIONS for w in cwords):
            return candidate.title()

    # Short whole-response heuristic (1–3 words that look like a name)
    raw_words = text.strip().split()
    # Strip leading/trailing punctuation from each token
    cleaned = [re.sub(r"^[^A-Za-z]+|[^A-Za-z\-']+$", "", w) for w in raw_words]
    cleaned = [w for w in cleaned if w]  # drop empty strings after stripping

    if 1 <= len(cleaned) <= 4 and all(re.match(r"[A-Za-z][A-Za-z\-']*$", w) for w in cleaned):
        lower_cleaned = [w.lower() for w in cleaned]
        # Reject if any word is a number, filler, time word, etc.
        if not any(w in _NAME_EXCLUSIONS for w in lower_cleaned):
            return " ".join(w.title() for w in cleaned)

    return None


def _extract_phone(text: str) -> str | None:
    # Digits (handles "555-123-4567", "+1 555 123 4567", etc.)
    digits = re.sub(r"[^\d+]", "", text)
    if len(digits) >= 10:
        return digits

    # Spoken word digits: "five five five one two three four five six seven"
    spoken_map = {
        "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    }
    words = text.lower().split()
    spoken_digits = [spoken_map[w] for w in words if w in spoken_map]
    if len(spoken_digits) >= 10:
        return "".join(spoken_digits)

    return None


def _extract_email(text: str) -> str | None:
    # Direct "@" email
    m = re.search(r"\b[\w._%+\-]+@[\w.\-]+\.[a-zA-Z]{2,}\b", text)
    if m:
        return m.group()

    # Spoken email: "john at gmail dot com"
    spoken = text.lower()
    spoken = re.sub(r"\bat\b", "@", spoken)
    spoken = re.sub(r"\bdot\b", ".", spoken)
    spoken = re.sub(r"\s+", "", spoken)
    m = re.search(r"\b[\w._%+\-]+@[\w.\-]+\.[a-zA-Z]{2,}\b", spoken)
    if m:
        return m.group()

    return None


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


def _service_names() -> list[str]:
    """Return a flat list of service name strings."""
    return [
        (s["name"] if isinstance(s, dict) else s)
        for s in get_services_flat()
    ]


def _services_message() -> str:
    names = _service_names()
    if not names:
        return "We offer a range of hair and beauty services. What are you looking for?"
    shown = names[:8]
    extra = len(names) - 8
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
        # All required fields collected.
        # Optional: ask for email once if not yet provided and not yet asked.
        email_already_asked = "email" in last_msg
        if not merged.email and not email_already_asked:
            # Customer may also skip by saying "skip", "no", "no email", etc.
            if intent in ("skip", "deny"):
                pass  # fall through to confirmation
            else:
                return AgentResponse(
                    message=(
                        "One last thing — could I get your email address for a booking confirmation? "
                        "Or just say 'skip' if you'd prefer not to."
                    ),
                    extracted_data=extracted,
                    action="continue",
                )

        # Build human-friendly confirmation
        d = _fmt_date(merged.preferred_date) if merged.preferred_date else "?"
        t = _fmt_time(merged.preferred_time) if merged.preferred_time else "?"
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
        names = _service_names()
        if names:
            sample = ", ".join(names[:5])
            extra = " and more" if len(names) > 5 else ""
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
