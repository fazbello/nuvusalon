#!/usr/bin/env python3
"""
Execution script: Send a test email.

Sends a test booking confirmation email to verify SendGrid is configured.

Usage:
    python execution/send_test_email.py recipient@example.com
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.integrations.email_sender import send_booking_confirmation, send_staff_notification
from app.models.appointment import AppointmentData


def main():
    if len(sys.argv) < 2:
        print("Usage: python execution/send_test_email.py <email_address>")
        sys.exit(1)

    email = sys.argv[1]

    appointment = AppointmentData(
        customer_name="Test Customer",
        phone_number="+11234567890",
        email=email,
        location="Downtown",
        service="Haircut & Style",
        technician="Maria Santos",
        preferred_date="2026-03-01",
        preferred_time="14:00",
        notes="Test booking — ignore this",
    )

    print(f"Sending confirmation email to {email}...")
    ok = send_booking_confirmation(appointment)
    print(f"  Confirmation: {'SENT' if ok else 'FAILED'}")

    print("Sending staff notification...")
    ok2 = send_staff_notification(appointment)
    print(f"  Staff alert: {'SENT' if ok2 else 'FAILED (check SALON_NOTIFICATION_EMAIL)'}")


if __name__ == "__main__":
    main()
