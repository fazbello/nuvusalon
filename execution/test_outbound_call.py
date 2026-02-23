#!/usr/bin/env python3
"""
Execution script: Test an outbound call.

Initiates a test outbound call to verify Twilio + Gemini integration
is working end-to-end.

Usage:
    python execution/test_outbound_call.py +11234567890
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.models.appointment import OutboundCallRequest
from app.voice.outbound import initiate_outbound_call


def main():
    if len(sys.argv) < 2:
        print("Usage: python execution/test_outbound_call.py <phone_number>")
        print("  Phone number must be in E.164 format (e.g. +11234567890)")
        sys.exit(1)

    phone = sys.argv[1]
    purpose = sys.argv[2] if len(sys.argv) > 2 else "appointment_confirmation"

    request = OutboundCallRequest(
        phone_number=phone,
        customer_name="Test Customer",
        purpose=purpose,
        appointment_details={
            "service": "Haircut & Style",
            "date": "2026-03-01",
            "time": "14:00",
            "technician": "Maria Santos",
        },
    )

    print(f"Initiating {purpose} call to {phone}...")
    try:
        result = initiate_outbound_call(request)
        print(f"Call initiated successfully!")
        print(f"  Call SID: {result['call_sid']}")
        print(f"  Status: {result['status']}")
    except Exception as exc:
        print(f"ERROR: {exc}")
        print()
        print("Check that TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_PHONE_NUMBER are set.")
        sys.exit(1)


if __name__ == "__main__":
    main()
