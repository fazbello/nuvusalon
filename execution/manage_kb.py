#!/usr/bin/env python3
"""
Execution script: Manage the salon knowledge base.

View, add technicians, add services, or validate the KB.

Usage:
    python execution/manage_kb.py list-technicians
    python execution/manage_kb.py list-services
    python execution/manage_kb.py add-technician "Name" "Title" "Specialty1,Specialty2" "mon,tue,wed"
    python execution/manage_kb.py validate
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

KB_PATH = Path("knowledge_base/salon_info.json")


def load_kb() -> dict:
    return json.loads(KB_PATH.read_text())


def save_kb(kb: dict) -> None:
    KB_PATH.write_text(json.dumps(kb, indent=2) + "\n")


def list_technicians():
    kb = load_kb()
    print("Technicians:")
    for t in kb.get("technicians", []):
        specs = ", ".join(t["specialties"])
        days = ", ".join(d.title() for d in t["available_days"])
        print(f"  {t['name']} ({t['title']})")
        print(f"    Specialties: {specs}")
        print(f"    Available: {days}")
        print()


def list_services():
    kb = load_kb()
    for cat in kb.get("services", []):
        print(f"\n{cat['category']}:")
        for item in cat["items"]:
            print(f"  - {item['name']} ({item['duration_minutes']} min) {item['price']}")


def add_technician(name: str, title: str, specialties_str: str, days_str: str):
    kb = load_kb()
    tech = {
        "name": name,
        "title": title,
        "specialties": [s.strip() for s in specialties_str.split(",")],
        "available_days": [d.strip().lower() for d in days_str.split(",")],
    }
    kb.setdefault("technicians", []).append(tech)
    save_kb(kb)
    print(f"Added technician: {name}")


def validate():
    kb = load_kb()
    errors = []
    if "salon" not in kb:
        errors.append("Missing 'salon' section")
    if "services" not in kb:
        errors.append("Missing 'services' section")
    if "technicians" not in kb:
        errors.append("Missing 'technicians' section")

    # Check technician specialties match actual services
    all_services = set()
    for cat in kb.get("services", []):
        for item in cat.get("items", []):
            all_services.add(item["name"])

    for tech in kb.get("technicians", []):
        for spec in tech.get("specialties", []):
            if spec not in all_services:
                errors.append(f"Technician '{tech['name']}' has unknown specialty: '{spec}'")

    if errors:
        print("Validation FAILED:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("Knowledge base is valid.")
        print(f"  {len(kb.get('technicians', []))} technicians")
        print(f"  {sum(len(c['items']) for c in kb.get('services', []))} services")
        print(f"  {len(kb.get('locations', []))} locations")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "list-technicians":
        list_technicians()
    elif cmd == "list-services":
        list_services()
    elif cmd == "add-technician" and len(sys.argv) >= 6:
        add_technician(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    elif cmd == "validate":
        validate()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
