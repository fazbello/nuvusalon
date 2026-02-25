"""
Local JSON-based storage for transcripts and incomplete bookings.

Acts as a primary store that always works, regardless of whether Google
Sheets or other external services are configured. Data lives in
knowledge_base/ so it persists across restarts (Railway ephemeral storage
aside — mount a volume for true persistence).

Transcripts:  knowledge_base/local_transcripts.json
Incomplete:   knowledge_base/incomplete_bookings.json
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE = Path("knowledge_base")
TRANSCRIPTS_FILE = _BASE / "local_transcripts.json"
INCOMPLETE_FILE = _BASE / "incomplete_bookings.json"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _load(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


# ── Transcript store ──────────────────────────────────────────────────────────

def save_transcript(
    call_sid: str,
    call_type: str,
    from_number: str,
    to_number: str,
    started_at: str,
    duration_seconds: int,
    transcript: str,
    appointment_booked: bool,
    appointment_data: dict | None,
) -> None:
    """Append a call transcript to the local JSON store."""
    try:
        data = _load(TRANSCRIPTS_FILE)
        records = data.setdefault("transcripts", [])
        records.append({
            "id": str(uuid.uuid4())[:8],
            "Date": started_at,
            "Call SID": call_sid,
            "Call Type": call_type,
            "From": from_number,
            "To": to_number,
            "Duration (s)": duration_seconds,
            "Transcript": transcript,
            "Appointment Booked": "Yes" if appointment_booked else "No",
            "appointment_data": appointment_data,
        })
        # Rolling window — keep newest 500
        data["transcripts"] = records[-500:]
        _save(TRANSCRIPTS_FILE, data)
    except Exception as exc:
        logger.warning("local_store.save_transcript failed [%s]: %s", type(exc).__name__, exc)


def get_local_transcripts(limit: int = 50) -> list[dict]:
    """Return recent transcripts, newest first."""
    try:
        data = _load(TRANSCRIPTS_FILE)
        records = data.get("transcripts", [])
        return list(reversed(records))[:limit]
    except Exception:
        return []


def get_local_appointments(limit: int = 50) -> list[dict]:
    """
    Return completed bookings extracted from local transcripts.
    Formatted to match the Google Sheets column names so the dashboard
    table renders identically whether data comes from Sheets or local.
    """
    try:
        records = get_local_transcripts(500)
        out = []
        for rec in records:
            if rec.get("Appointment Booked") != "Yes":
                continue
            appt = rec.get("appointment_data") or {}
            out.append({
                "Date Booked": rec.get("Date", ""),
                "Full Name": appt.get("customer_name", ""),
                "Phone Number": appt.get("phone_number", "") or rec.get("From", ""),
                "Email": appt.get("email", ""),
                "Location": appt.get("location", ""),
                "Service": appt.get("service", ""),
                "Preferred Technician": appt.get("technician", "") or "No preference",
                "Appointment Date": appt.get("preferred_date", ""),
                "Appointment Time": appt.get("preferred_time", ""),
                "Status": "Confirmed",
                "Notes": appt.get("notes", ""),
                "Calendar Link": "",
            })
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


# ── Incomplete booking store ──────────────────────────────────────────────────

def save_incomplete_booking(
    appointment,  # AppointmentData
    call_sid: str,
    from_number: str,
    duration_seconds: int,
) -> None:
    """
    Save partial booking data when a call ends without completing a booking
    but at least one meaningful field was collected.

    Only saves if there's something worth following up on (i.e. not just
    a call where the customer immediately hung up with no data).
    """
    try:
        appt_dict = appointment.model_dump()
        # Need at least a service or a name (phone is always present from caller ID)
        meaningful = {k: v for k, v in appt_dict.items()
                      if k not in ("phone_number",) and v}
        if not meaningful:
            return

        data = _load(INCOMPLETE_FILE)
        bookings = data.setdefault("bookings", [])
        bookings.append({
            "id": str(uuid.uuid4())[:8],
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "call_sid": call_sid,
            "from_number": from_number,
            "duration_seconds": duration_seconds,
            "appointment": appt_dict,
            "fields_collected": list(meaningful.keys()),
            "dismissed": False,
        })
        # Rolling window — keep newest 200
        data["bookings"] = bookings[-200:]
        _save(INCOMPLETE_FILE, data)
        logger.info(
            "Incomplete booking saved for %s — fields: %s",
            from_number, list(meaningful.keys()),
        )
    except Exception as exc:
        logger.warning(
            "local_store.save_incomplete_booking failed [%s]: %s",
            type(exc).__name__, exc,
        )


def get_incomplete_bookings(include_dismissed: bool = False) -> list[dict]:
    """Return incomplete bookings, newest first."""
    try:
        data = _load(INCOMPLETE_FILE)
        bookings = data.get("bookings", [])
        if not include_dismissed:
            bookings = [b for b in bookings if not b.get("dismissed")]
        return list(reversed(bookings))
    except Exception:
        return []


def dismiss_incomplete_booking(booking_id: str) -> bool:
    """Mark an incomplete booking as dismissed so it leaves the action queue."""
    try:
        data = _load(INCOMPLETE_FILE)
        for booking in data.get("bookings", []):
            if booking.get("id") == booking_id:
                booking["dismissed"] = True
                booking["dismissed_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                _save(INCOMPLETE_FILE, data)
                return True
        return False
    except Exception as exc:
        logger.warning(
            "local_store.dismiss_incomplete_booking failed [%s]: %s",
            type(exc).__name__, exc,
        )
        return False
