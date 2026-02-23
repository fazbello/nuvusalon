"""
Google Sheets integration.

Logs appointments and call transcripts to a shared Google Sheet
so the salon owner has a live dashboard of all activity.
"""

from __future__ import annotations

import logging
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from app.config import get_settings
from app.models.appointment import AppointmentData, TranscriptRecord

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Sheet tab names
APPOINTMENTS_TAB = "Appointments"
TRANSCRIPTS_TAB = "Transcripts"
CUSTOMERS_TAB = "Customers"

APPOINTMENTS_HEADERS = [
    "Date Booked",
    "Full Name",
    "Phone Number",
    "Email",
    "Location",
    "Service",
    "Preferred Technician",
    "Appointment Date",
    "Appointment Time",
    "Status",
    "Notes",
    "Calendar Link",
]

TRANSCRIPTS_HEADERS = [
    "Date",
    "Call SID",
    "Call Type",
    "From",
    "To",
    "Duration (s)",
    "Transcript",
    "Appointment Booked",
]

CUSTOMERS_HEADERS = [
    "Full Name",
    "Phone Number",
    "Email",
    "Location",
    "First Visit",
    "Last Visit",
    "Total Visits",
]


def _get_client() -> gspread.Client:
    settings = get_settings()
    creds_info = settings.get_google_credentials_info()
    if not creds_info:
        raise RuntimeError("Google credentials not configured.")
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_or_create_sheet(
    spreadsheet: gspread.Spreadsheet,
    title: str,
    headers: list[str],
) -> gspread.Worksheet:
    """Get existing worksheet or create with headers."""
    try:
        ws = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(headers))
        ws.append_row(headers, value_input_option="USER_ENTERED")
        # Bold the header row
        ws.format("1", {"textFormat": {"bold": True}})
    return ws


def setup_spreadsheet() -> str:
    """
    Ensure the spreadsheet has all required tabs with headers.
    Returns the spreadsheet URL.
    """
    settings = get_settings()
    client = _get_client()
    spreadsheet = client.open_by_key(settings.google_sheet_id)

    _get_or_create_sheet(spreadsheet, APPOINTMENTS_TAB, APPOINTMENTS_HEADERS)
    _get_or_create_sheet(spreadsheet, TRANSCRIPTS_TAB, TRANSCRIPTS_HEADERS)
    _get_or_create_sheet(spreadsheet, CUSTOMERS_TAB, CUSTOMERS_HEADERS)

    url = spreadsheet.url
    logger.info("Spreadsheet ready: %s", url)
    return url


def log_appointment(appointment: AppointmentData, calendar_link: str = "") -> None:
    """Append a new appointment row to the Appointments tab."""
    settings = get_settings()
    client = _get_client()
    spreadsheet = client.open_by_key(settings.google_sheet_id)
    ws = _get_or_create_sheet(spreadsheet, APPOINTMENTS_TAB, APPOINTMENTS_HEADERS)

    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        appointment.customer_name or "",
        appointment.phone_number or "",
        appointment.email or "",
        appointment.location or "",
        appointment.service or "",
        appointment.technician or "No preference",
        appointment.preferred_date or "",
        appointment.preferred_time or "",
        "Confirmed",
        appointment.notes or "",
        calendar_link,
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")
    logger.info("Logged appointment for %s", appointment.customer_name)

    # Also upsert customer record
    _upsert_customer(spreadsheet, appointment)


def log_transcript(record: TranscriptRecord) -> None:
    """Append a call transcript to the Transcripts tab."""
    settings = get_settings()
    client = _get_client()
    spreadsheet = client.open_by_key(settings.google_sheet_id)
    ws = _get_or_create_sheet(spreadsheet, TRANSCRIPTS_TAB, TRANSCRIPTS_HEADERS)

    row = [
        record.started_at,
        record.call_sid,
        record.call_type.value,
        record.from_number,
        record.to_number,
        str(record.duration_seconds),
        record.transcript,
        "Yes" if record.appointment_booked else "No",
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")
    logger.info("Logged transcript for call %s", record.call_sid)


def _upsert_customer(
    spreadsheet: gspread.Spreadsheet,
    appointment: AppointmentData,
) -> None:
    """Add new customer or update visit count for returning customer."""
    ws = _get_or_create_sheet(spreadsheet, CUSTOMERS_TAB, CUSTOMERS_HEADERS)
    now = datetime.now().strftime("%Y-%m-%d")

    # Search by phone number
    try:
        phone_cell = ws.find(appointment.phone_number)
    except gspread.CellNotFound:
        phone_cell = None

    if phone_cell:
        row_num = phone_cell.row
        # Update last visit and increment total visits
        ws.update_cell(row_num, 6, now)  # Last Visit
        current_visits = ws.cell(row_num, 7).value
        ws.update_cell(row_num, 7, int(current_visits or 0) + 1)
    else:
        # New customer
        row = [
            appointment.customer_name or "",
            appointment.phone_number or "",
            appointment.email or "",
            appointment.location or "",
            now,  # First Visit
            now,  # Last Visit
            1,    # Total Visits
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")
