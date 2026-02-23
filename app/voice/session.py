"""
In-memory call session store.

Tracks conversation state, collected appointment data, and
full transcript for every active call (keyed by Twilio CallSid).

For multi-instance scaling, swap this for Redis.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from app.models.appointment import AppointmentData, CallType, ConversationState

logger = logging.getLogger(__name__)


class CallSession:
    """State for a single phone call."""

    def __init__(
        self,
        call_sid: str,
        from_number: str,
        to_number: str,
        call_type: CallType,
        purpose: str = "",
        context: str = "",
    ):
        self.call_sid = call_sid
        self.from_number = from_number
        self.to_number = to_number
        self.call_type = call_type
        self.state = ConversationState.GREETING
        self.appointment = AppointmentData(phone_number=from_number)
        self.history: list[dict] = []  # [{"role": "customer"|"agent", "content": "..."}]
        self.purpose = purpose
        self.context = context
        self.created_at = datetime.utcnow()
        self.appointment_booked = False

    def add_customer_message(self, text: str) -> None:
        self.history.append({"role": "customer", "content": text})

    def add_agent_message(self, text: str) -> None:
        self.history.append({"role": "agent", "content": text})

    def update_appointment(self, extracted: dict) -> None:
        """Merge newly extracted fields into the appointment."""
        for key, value in extracted.items():
            if value and hasattr(self.appointment, key):
                setattr(self.appointment, key, value)

    def get_transcript(self) -> str:
        """Return the full conversation as plain text."""
        lines = []
        for msg in self.history:
            role = "Customer" if msg["role"] == "customer" else "Agent"
            lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines)

    def duration_seconds(self) -> int:
        return int((datetime.utcnow() - self.created_at).total_seconds())


# ── Session Store ──────────────────────────────────────────────

_sessions: dict[str, CallSession] = {}


def create_session(
    call_sid: str,
    from_number: str,
    to_number: str,
    call_type: CallType,
    purpose: str = "",
    context: str = "",
) -> CallSession:
    session = CallSession(
        call_sid=call_sid,
        from_number=from_number,
        to_number=to_number,
        call_type=call_type,
        purpose=purpose,
        context=context,
    )
    _sessions[call_sid] = session
    logger.info("Created %s session %s from %s", call_type.value, call_sid, from_number)
    return session


def get_session(call_sid: str) -> Optional[CallSession]:
    return _sessions.get(call_sid)


def end_session(call_sid: str) -> Optional[CallSession]:
    return _sessions.pop(call_sid, None)


def get_active_sessions() -> dict[str, CallSession]:
    return dict(_sessions)
