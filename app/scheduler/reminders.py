"""
Appointment reminder scheduler.

Uses APScheduler to periodically scan upcoming appointments
in Google Calendar and send reminder emails + optional reminder calls.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from app.config import get_settings
from app.integrations.email_sender import send_appointment_reminder
from app.models.appointment import AppointmentData

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# Track which events we've already sent reminders for (avoid duplicates)
_reminded: set[str] = set()

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def _get_calendar_service():
    settings = get_settings()
    creds_info = settings.get_google_credentials_info()
    if not creds_info:
        return None
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds)


async def check_and_send_reminders() -> None:
    """
    Find appointments happening in the next N hours and
    send reminder emails to customers who haven't been reminded yet.
    """
    settings = get_settings()
    service = _get_calendar_service()
    if not service:
        logger.warning("Calendar not configured — skipping reminder check")
        return

    now = datetime.utcnow()
    window_end = now + timedelta(hours=settings.reminder_hours_before)

    try:
        events_result = (
            service.events()
            .list(
                calendarId=settings.google_calendar_id,
                timeMin=now.isoformat() + "Z",
                timeMax=window_end.isoformat() + "Z",
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except Exception as exc:
        logger.error("Failed to fetch calendar events: %s", exc)
        return

    events = events_result.get("items", [])
    logger.info("Found %d upcoming events in reminder window", len(events))

    for event in events:
        event_id = event.get("id", "")
        if event_id in _reminded:
            continue

        # Parse appointment info from event description
        description = event.get("description", "")
        attendees = event.get("attendees", [])
        customer_email = attendees[0]["email"] if attendees else ""

        if not customer_email:
            continue

        # Extract info from description
        appointment = _parse_event_to_appointment(event, customer_email)

        try:
            sent = send_appointment_reminder(appointment)
            if sent:
                _reminded.add(event_id)
                logger.info("Sent reminder to %s for event %s", customer_email, event_id)
        except Exception as exc:
            logger.error("Failed to send reminder for event %s: %s", event_id, exc)


def _parse_event_to_appointment(event: dict, email: str) -> AppointmentData:
    """Extract appointment data from a Google Calendar event."""
    description = event.get("description", "")
    lines = description.split("\n")
    data: dict = {"email": email}

    for line in lines:
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip().lower()
            val = val.strip()
            if "customer" in key or "name" in key:
                data["customer_name"] = val
            elif "phone" in key:
                data["phone_number"] = val
            elif "service" in key:
                data["service"] = val
            elif "technician" in key:
                data["technician"] = val

    start = event.get("start", {}).get("dateTime", "")
    if start:
        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        data["preferred_date"] = dt.strftime("%Y-%m-%d")
        data["preferred_time"] = dt.strftime("%H:%M")

    return AppointmentData(**data)


def start_scheduler() -> None:
    """Start the reminder check job (runs every 30 minutes)."""
    scheduler.add_job(
        check_and_send_reminders,
        "interval",
        minutes=30,
        id="reminder_check",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Reminder scheduler started (checking every 30 minutes)")


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Reminder scheduler stopped")
