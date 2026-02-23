"""
Load and query the salon knowledge base.

The KB is a JSON file that can be edited by non-technical staff.
This module exposes simple helpers so the AI agent can look up
services, technicians, hours, and policies without parsing JSON itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from app.config import get_settings


_kb_cache: dict | None = None


def _load() -> dict:
    global _kb_cache
    if _kb_cache is not None:
        return _kb_cache
    path = Path(get_settings().knowledge_base_path)
    if not path.exists():
        # Fall back to the bundled default
        path = Path(__file__).parent / "default_kb.json"
    _kb_cache = json.loads(path.read_text()) if path.exists() else {}
    return _kb_cache


def reload() -> dict:
    """Force-reload (useful after KB edits)."""
    global _kb_cache
    _kb_cache = None
    return _load()


def get_full_kb() -> dict:
    return _load()


def get_salon_info() -> dict:
    return _load().get("salon", {})


def get_locations() -> list[dict]:
    return _load().get("locations", [])


def get_services_flat() -> list[dict]:
    """Return every service as a flat list with its category."""
    flat: list[dict] = []
    for cat in _load().get("services", []):
        for item in cat.get("items", []):
            flat.append({**item, "category": cat["category"]})
    return flat


def get_service_by_name(name: str) -> Optional[dict]:
    """Case-insensitive fuzzy lookup."""
    lower = name.lower()
    for svc in get_services_flat():
        if lower in svc["name"].lower():
            return svc
    return None


def get_technicians() -> list[dict]:
    return _load().get("technicians", [])


def get_technician_by_name(name: str) -> Optional[dict]:
    lower = name.lower()
    for tech in get_technicians():
        if lower in tech["name"].lower():
            return tech
    return None


def get_technicians_for_service(service_name: str) -> list[dict]:
    """Return technicians who offer a given service."""
    lower = service_name.lower()
    return [
        t for t in get_technicians()
        if any(lower in s.lower() for s in t.get("specialties", []))
    ]


def get_policies() -> dict:
    return _load().get("policies", {})


def get_faq() -> list[dict]:
    return _load().get("faq", [])


def get_kb_summary_for_agent() -> str:
    """
    Return a formatted text summary of the entire KB
    suitable for injecting into the Gemini system prompt.
    """
    kb = _load()
    lines: list[str] = []
    salon = kb.get("salon", {})
    lines.append(f"## {salon.get('name', 'Salon')}")
    lines.append(f"Tagline: {salon.get('tagline', '')}")
    lines.append("")

    # Locations
    for loc in kb.get("locations", []):
        lines.append(f"### Location: {loc['name']}")
        lines.append(f"Address: {loc['address']}")
        hours = loc.get("hours", {})
        lines.append("Hours: " + ", ".join(
            f"{day.title()}: {time}" for day, time in hours.items()
        ))
        lines.append("")

    # Services
    lines.append("### Services & Pricing")
    for cat in kb.get("services", []):
        lines.append(f"\n**{cat['category']}**")
        for item in cat["items"]:
            lines.append(
                f"  - {item['name']} ({item['duration_minutes']} min) — {item['price']}"
            )
    lines.append("")

    # Technicians
    lines.append("### Technicians")
    for tech in kb.get("technicians", []):
        specs = ", ".join(tech.get("specialties", []))
        days = ", ".join(d.title() for d in tech.get("available_days", []))
        lines.append(f"  - {tech['name']} ({tech['title']}) — {specs}")
        lines.append(f"    Available: {days}")
    lines.append("")

    # Policies
    lines.append("### Policies")
    for key, val in kb.get("policies", {}).items():
        lines.append(f"  - **{key.replace('_', ' ').title()}:** {val}")
    lines.append("")

    # FAQ
    lines.append("### FAQ")
    for faq in kb.get("faq", []):
        lines.append(f"  Q: {faq['question']}")
        lines.append(f"  A: {faq['answer']}")
        lines.append("")

    return "\n".join(lines)
