"""
Email integration via SendGrid.

Sends booking confirmations to customers, reminder emails,
and notification alerts to salon staff.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, HtmlContent

from app.config import get_settings
from app.models.appointment import AppointmentData

logger = logging.getLogger(__name__)


def _get_client() -> SendGridAPIClient:
    return SendGridAPIClient(get_settings().sendgrid_api_key)


def _send(to_email: str, subject: str, html_body: str) -> bool:
    """Send a single email. Returns True on success."""
    settings = get_settings()
    message = Mail(
        from_email=settings.from_email,
        to_emails=to_email,
        subject=subject,
        html_content=HtmlContent(html_body),
    )
    try:
        response = _get_client().send(message)
        logger.info(
            "Email sent to %s — status %s", to_email, response.status_code
        )
        return response.status_code in (200, 201, 202)
    except Exception as exc:
        logger.error("Failed to send email to %s: %s", to_email, exc)
        return False


# ── Email Templates ────────────────────────────────────────────

def _confirmation_html(appointment: AppointmentData) -> str:
    settings = get_settings()
    return f"""\
<html>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
  <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 10px 10px 0 0;">
    <h1 style="color: white; margin: 0;">{settings.salon_name}</h1>
    <p style="color: #e0e0ff; margin: 5px 0 0;">Appointment Confirmation</p>
  </div>
  <div style="border: 1px solid #e0e0e0; border-top: none; padding: 25px; border-radius: 0 0 10px 10px;">
    <p>Hi <strong>{appointment.customer_name}</strong>,</p>
    <p>Your appointment has been confirmed! Here are the details:</p>
    <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
      <tr style="border-bottom: 1px solid #f0f0f0;">
        <td style="padding: 10px; font-weight: bold; color: #555;">Service</td>
        <td style="padding: 10px;">{appointment.service}</td>
      </tr>
      <tr style="border-bottom: 1px solid #f0f0f0;">
        <td style="padding: 10px; font-weight: bold; color: #555;">Date</td>
        <td style="padding: 10px;">{appointment.preferred_date}</td>
      </tr>
      <tr style="border-bottom: 1px solid #f0f0f0;">
        <td style="padding: 10px; font-weight: bold; color: #555;">Time</td>
        <td style="padding: 10px;">{appointment.preferred_time}</td>
      </tr>
      <tr style="border-bottom: 1px solid #f0f0f0;">
        <td style="padding: 10px; font-weight: bold; color: #555;">Technician</td>
        <td style="padding: 10px;">{appointment.technician or 'First available'}</td>
      </tr>
      <tr>
        <td style="padding: 10px; font-weight: bold; color: #555;">Location</td>
        <td style="padding: 10px;">{appointment.location or 'Main location'}</td>
      </tr>
    </table>
    <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; margin: 20px 0;">
      <p style="margin: 0; font-size: 14px; color: #666;">
        <strong>Cancellation Policy:</strong> Please cancel or reschedule at least
        24 hours before your appointment to avoid a cancellation fee.
      </p>
    </div>
    <p>We look forward to seeing you!</p>
    <p style="color: #888; font-size: 12px;">— The {settings.salon_name} Team</p>
  </div>
</body>
</html>"""


def _reminder_html(appointment: AppointmentData) -> str:
    settings = get_settings()
    return f"""\
<html>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
  <div style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); padding: 30px; border-radius: 10px 10px 0 0;">
    <h1 style="color: white; margin: 0;">{settings.salon_name}</h1>
    <p style="color: #ffe0e8; margin: 5px 0 0;">Appointment Reminder</p>
  </div>
  <div style="border: 1px solid #e0e0e0; border-top: none; padding: 25px; border-radius: 0 0 10px 10px;">
    <p>Hi <strong>{appointment.customer_name}</strong>,</p>
    <p>This is a friendly reminder about your upcoming appointment:</p>
    <div style="background: #fff3f5; padding: 20px; border-radius: 8px; border-left: 4px solid #f5576c; margin: 20px 0;">
      <p style="margin: 5px 0;"><strong>{appointment.service}</strong></p>
      <p style="margin: 5px 0;">Date: {appointment.preferred_date} at {appointment.preferred_time}</p>
      <p style="margin: 5px 0;">Technician: {appointment.technician or 'First available'}</p>
    </div>
    <p>Need to reschedule? Please call us at least 24 hours before your appointment.</p>
    <p>See you soon!</p>
    <p style="color: #888; font-size: 12px;">— The {settings.salon_name} Team</p>
  </div>
</body>
</html>"""


def _staff_notification_html(appointment: AppointmentData) -> str:
    settings = get_settings()
    return f"""\
<html>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
  <div style="background: #2d3436; padding: 20px; border-radius: 10px 10px 0 0;">
    <h2 style="color: white; margin: 0;">New Booking Alert</h2>
  </div>
  <div style="border: 1px solid #e0e0e0; border-top: none; padding: 25px; border-radius: 0 0 10px 10px;">
    <p>A new appointment has been booked via the AI phone agent:</p>
    <table style="width: 100%; border-collapse: collapse;">
      <tr style="border-bottom: 1px solid #f0f0f0;">
        <td style="padding: 8px; font-weight: bold;">Customer</td>
        <td style="padding: 8px;">{appointment.customer_name}</td>
      </tr>
      <tr style="border-bottom: 1px solid #f0f0f0;">
        <td style="padding: 8px; font-weight: bold;">Phone</td>
        <td style="padding: 8px;">{appointment.phone_number}</td>
      </tr>
      <tr style="border-bottom: 1px solid #f0f0f0;">
        <td style="padding: 8px; font-weight: bold;">Email</td>
        <td style="padding: 8px;">{appointment.email}</td>
      </tr>
      <tr style="border-bottom: 1px solid #f0f0f0;">
        <td style="padding: 8px; font-weight: bold;">Service</td>
        <td style="padding: 8px;">{appointment.service}</td>
      </tr>
      <tr style="border-bottom: 1px solid #f0f0f0;">
        <td style="padding: 8px; font-weight: bold;">Technician</td>
        <td style="padding: 8px;">{appointment.technician or 'No preference'}</td>
      </tr>
      <tr style="border-bottom: 1px solid #f0f0f0;">
        <td style="padding: 8px; font-weight: bold;">Date</td>
        <td style="padding: 8px;">{appointment.preferred_date}</td>
      </tr>
      <tr style="border-bottom: 1px solid #f0f0f0;">
        <td style="padding: 8px; font-weight: bold;">Time</td>
        <td style="padding: 8px;">{appointment.preferred_time}</td>
      </tr>
      <tr>
        <td style="padding: 8px; font-weight: bold;">Notes</td>
        <td style="padding: 8px;">{appointment.notes or 'None'}</td>
      </tr>
    </table>
    <p style="color: #888; font-size: 12px; margin-top: 20px;">
      Booked at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} via {settings.app_name}
    </p>
  </div>
</body>
</html>"""


# ── Public API ─────────────────────────────────────────────────

def send_booking_confirmation(appointment: AppointmentData) -> bool:
    """Send confirmation email to customer after booking."""
    if not appointment.email:
        logger.warning("No email for customer %s — skipping confirmation", appointment.customer_name)
        return False
    return _send(
        to_email=appointment.email,
        subject=f"Appointment Confirmed — {get_settings().salon_name}",
        html_body=_confirmation_html(appointment),
    )


def send_appointment_reminder(appointment: AppointmentData) -> bool:
    """Send reminder email to customer before appointment."""
    if not appointment.email:
        return False
    return _send(
        to_email=appointment.email,
        subject=f"Reminder: Your appointment is coming up — {get_settings().salon_name}",
        html_body=_reminder_html(appointment),
    )


def send_staff_notification(appointment: AppointmentData) -> bool:
    """Alert salon staff about a new booking."""
    settings = get_settings()
    if not settings.salon_notification_email:
        logger.warning("No salon notification email configured — skipping staff alert")
        return False
    return _send(
        to_email=settings.salon_notification_email,
        subject=f"New Booking: {appointment.customer_name} — {appointment.service}",
        html_body=_staff_notification_html(appointment),
    )
