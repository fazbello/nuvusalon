"""
Call-data learner — improves the system from every completed call.

After each call this module:
  1. Updates call statistics (total calls, bookings, popular services,
     peak hours, peak days)
  2. Logs unknown phrases (what callers said that the rule engine
     couldn't handle) for admin review so they can be added to FAQ
  3. Tracks service popularity so admins can see demand trends

No API calls — pure data collection from calls the system already handled.
Admins review insights via the dashboard Insights tab.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

STATS_FILE = Path("knowledge_base/call_stats.json")
UNKNOWNS_FILE = Path("knowledge_base/unknown_phrases.json")


# ── Public API ────────────────────────────────────────────────────────────────

def record_call(
    call_type: str,
    appointment_data: dict | None = None,
    booked: bool = False,
) -> None:
    """
    Call this after every completed call.
    Increments counters and records service/time statistics.
    """
    try:
        stats = _load_stats()
        now = datetime.now()

        stats["total_calls"] = stats.get("total_calls", 0) + 1
        stats["inbound_calls" if call_type == "inbound" else "outbound_calls"] = (
            stats.get("inbound_calls" if call_type == "inbound" else "outbound_calls", 0) + 1
        )

        if booked and appointment_data:
            stats["total_bookings"] = stats.get("total_bookings", 0) + 1
            service = appointment_data.get("service")
            if service:
                counts = stats.setdefault("service_counts", {})
                counts[service] = counts.get(service, 0) + 1

        # Peak hour (0-23) and day of week
        hour_key = str(now.hour)
        day_key = now.strftime("%A").lower()
        stats.setdefault("peak_hours", {})[hour_key] = (
            stats.get("peak_hours", {}).get(hour_key, 0) + 1
        )
        stats.setdefault("peak_days", {})[day_key] = (
            stats.get("peak_days", {}).get(day_key, 0) + 1
        )

        stats["last_updated"] = now.isoformat()
        _save_stats(stats)
    except Exception as exc:
        logger.warning("learner.record_call failed: %s", exc)


def log_unknown_phrase(phrase: str) -> None:
    """
    Save a phrase the rule engine couldn't match, for admin review.
    Admins can then add it as a FAQ entry in the knowledge base.
    Phrases are deduplicated; top 100 kept by recency.
    """
    phrase = phrase.strip()
    if not phrase or len(phrase) < 4:
        return
    try:
        data = _load_unknowns()
        phrases = data.setdefault("unknown_phrases", [])

        for entry in phrases:
            if entry.get("phrase", "").lower() == phrase.lower():
                entry["count"] = entry.get("count", 1) + 1
                entry["last_seen"] = datetime.now().isoformat()
                break
        else:
            phrases.append({
                "phrase": phrase,
                "count": 1,
                "first_seen": datetime.now().isoformat(),
                "last_seen": datetime.now().isoformat(),
                "reviewed": False,
            })

        # Keep most recent 100, sorted by frequency then recency
        phrases.sort(key=lambda x: (-x.get("count", 1), x.get("last_seen", "")))
        data["unknown_phrases"] = phrases[:100]
        _save_unknowns(data)
    except Exception as exc:
        logger.warning("learner.log_unknown_phrase failed: %s", exc)


def mark_reviewed(phrase: str) -> None:
    """Mark an unknown phrase as reviewed by admin."""
    try:
        data = _load_unknowns()
        for entry in data.get("unknown_phrases", []):
            if entry.get("phrase") == phrase:
                entry["reviewed"] = True
                entry["reviewed_at"] = datetime.now().isoformat()
        _save_unknowns(data)
    except Exception as exc:
        logger.warning("learner.mark_reviewed failed: %s", exc)


def get_stats() -> dict:
    """Return the full stats dict for the dashboard."""
    stats = _load_stats()

    # Derived: top 5 services by booking count
    svc = stats.get("service_counts", {})
    stats["top_services"] = sorted(svc.items(), key=lambda x: -x[1])[:5]

    # Peak hour label (e.g. "2 PM")
    peak_hours = stats.get("peak_hours", {})
    if peak_hours:
        best = max(peak_hours, key=lambda h: peak_hours[h])
        h = int(best)
        stats["peak_hour_label"] = f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"
    else:
        stats["peak_hour_label"] = "N/A"

    # Peak day label
    peak_days = stats.get("peak_days", {})
    if peak_days:
        stats["peak_day_label"] = max(peak_days, key=lambda d: peak_days[d]).title()
    else:
        stats["peak_day_label"] = "N/A"

    return stats


def get_unknown_phrases(limit: int = 50) -> list[dict]:
    """Return unknown phrases sorted by frequency, unreviewed first."""
    data = _load_unknowns()
    phrases = data.get("unknown_phrases", [])
    phrases.sort(key=lambda x: (x.get("reviewed", False), -x.get("count", 1)))
    return phrases[:limit]


# ── Storage helpers ───────────────────────────────────────────────────────────

def _load_stats() -> dict:
    if STATS_FILE.exists():
        try:
            return json.loads(STATS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_stats(data: dict) -> None:
    STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATS_FILE.write_text(json.dumps(data, indent=2))


def _load_unknowns() -> dict:
    if UNKNOWNS_FILE.exists():
        try:
            return json.loads(UNKNOWNS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_unknowns(data: dict) -> None:
    UNKNOWNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    UNKNOWNS_FILE.write_text(json.dumps(data, indent=2))
