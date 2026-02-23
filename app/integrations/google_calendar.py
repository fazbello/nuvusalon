"""
Google Calendar integration.

Creates calendar events for booked appointments and checks
technician availability against existing events.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from app.config import get_settings
from app.models.appointment import AppointmentData

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _get_service():
    settings = get_settings()
    creds_info = settings.get_google_credentials_info()
    if not creds_info:
        raise RuntimeError(
            "Google credentials not configured. "
            "Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS."
        )
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds)


def create_appointment_event(appointment: AppointmentData) -> dict:
    """
    Create a Google Calendar event for a confirmed appointment.
    Returns the created event resource (including htmlLink).
    """
    settings = get_settings()
    service = _get_service()

    start_dt = datetime.strptime(
        f"{appointment.preferred_date} {appointment.preferred_time}",
        "%Y-%m-%d %H:%M",
    )
    end_dt = start_dt + timedelta(minutes=settings.appointment_duration_minutes)

    # Look up service duration from KB for more accurate end time
    from app.knowledge_base.loader import get_service_by_name

    svc = get_service_by_name(appointment.service) if appointment.service else None
    if svc and svc.get("duration_minutes"):
        end_dt = start_dt + timedelta(minutes=svc["duration_minutes"])

    event_body = {
        "summary": f"{appointment.service} — {appointment.customer_name}",
        "description": (
            f"Customer: {appointment.customer_name}\n"
            f"Phone: {appointment.phone_number}\n"
            f"Email: {appointment.email}\n"
            f"Service: {appointment.service}\n"
            f"Technician: {appointment.technician or 'No preference'}\n"
            f"Notes: {appointment.notes or 'None'}"
        ),
        "start": {
            "dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": settings.salon_timezone,
        },
        "end": {
            "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": settings.salon_timezone,
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 24 * 60},
                {"method": "popup", "minutes": 60},
            ],
        },
    }

    # Add customer as attendee so they get a Calendar invite
    if appointment.email:
        event_body["attendees"] = [{"email": appointment.email}]

    event = (
        service.events()
        .insert(
            calendarId=settings.google_calendar_id,
            body=event_body,
            sendUpdates="all",  # Sends invite email to attendees
        )
        .execute()
    )
    logger.info("Created calendar event: %s", event.get("htmlLink"))
    return event


def check_availability(
    date_str: str,
    time_str: str,
    duration_minutes: int = 60,
) -> bool:
    """
    Check if a time slot is available on the salon calendar.
    Returns True if the slot is free.
    """
    settings = get_settings()
    service = _get_service()

    start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(minutes=duration_minutes)

    events_result = (
        service.events()
        .list(
            calendarId=settings.google_calendar_id,
            timeMin=start_dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
            timeMax=end_dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    events = events_result.get("items", [])
    return len(events) == 0


def get_available_slots(date_str: str, duration_minutes: int = 60) -> list[str]:
    """
    Return available time slots for a given date.
    Checks every 30-minute window during business hours (9 AM - 6 PM).
    """
    settings = get_settings()
    service = _get_service()

    day_start = datetime.strptime(f"{date_str} 09:00", "%Y-%m-%d %H:%M")
    day_end = datetime.strptime(f"{date_str} 18:00", "%Y-%m-%d %H:%M")

    events_result = (
        service.events()
        .list(
            calendarId=settings.google_calendar_id,
            timeMin=day_start.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
            timeMax=day_end.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    busy_periods = []
    for event in events_result.get("items", []):
        start = event["start"].get("dateTime", "")
        end = event["end"].get("dateTime", "")
        if start and end:
            busy_periods.append((
                datetime.fromisoformat(start.replace("Z", "+00:00")),
                datetime.fromisoformat(end.replace("Z", "+00:00")),
            ))

    available: list[str] = []
    current = day_start
    while current + timedelta(minutes=duration_minutes) <= day_end:
        slot_end = current + timedelta(minutes=duration_minutes)
        is_free = all(
            slot_end <= busy_start or current >= busy_end
            for busy_start, busy_end in busy_periods
        )
        if is_free:
            available.append(current.strftime("%H:%M"))
        current += timedelta(minutes=30)

    return available
