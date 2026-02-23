"""
Data models for appointments and customer records.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class CallType(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class ConversationState(str, Enum):
    GREETING = "greeting"
    COLLECTING = "collecting"
    CONFIRMING = "confirming"
    BOOKING = "booking"
    COMPLETE = "complete"
    TRANSFER = "transfer"


class AppointmentStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


class AppointmentData(BaseModel):
    """All fields the agent needs to collect before booking."""
    customer_name: Optional[str] = None
    phone_number: Optional[str] = None
    email: Optional[str] = None
    location: Optional[str] = None
    service: Optional[str] = None
    technician: Optional[str] = None
    preferred_date: Optional[str] = None  # YYYY-MM-DD
    preferred_time: Optional[str] = None  # HH:MM
    notes: Optional[str] = None

    def missing_required_fields(self) -> list[str]:
        """Return names of fields still needed to book."""
        required = {
            "customer_name": self.customer_name,
            "phone_number": self.phone_number,
            "email": self.email,
            "service": self.service,
            "preferred_date": self.preferred_date,
            "preferred_time": self.preferred_time,
        }
        return [k for k, v in required.items() if not v]

    def is_complete(self) -> bool:
        return len(self.missing_required_fields()) == 0

    def summary(self) -> str:
        parts = []
        if self.customer_name:
            parts.append(f"Name: {self.customer_name}")
        if self.phone_number:
            parts.append(f"Phone: {self.phone_number}")
        if self.email:
            parts.append(f"Email: {self.email}")
        if self.service:
            parts.append(f"Service: {self.service}")
        if self.technician:
            parts.append(f"Technician: {self.technician}")
        if self.preferred_date:
            parts.append(f"Date: {self.preferred_date}")
        if self.preferred_time:
            parts.append(f"Time: {self.preferred_time}")
        if self.location:
            parts.append(f"Location: {self.location}")
        if self.notes:
            parts.append(f"Notes: {self.notes}")
        return "\n".join(parts) if parts else "(no data collected yet)"


class AgentResponse(BaseModel):
    """Structured response from the Gemini conversation agent."""
    message: str = Field(description="What to say to the customer")
    extracted_data: dict = Field(
        default_factory=dict,
        description="Any new appointment fields extracted from the customer's speech",
    )
    action: str = Field(
        default="continue",
        description="Next action: continue | confirm | book | transfer | end",
    )


class OutboundCallRequest(BaseModel):
    """Request body to trigger an outbound call."""
    phone_number: str = Field(description="Customer phone in E.164 format")
    customer_name: Optional[str] = None
    purpose: str = Field(
        default="appointment_confirmation",
        description="Purpose: appointment_confirmation | reminder | follow_up | custom",
    )
    appointment_details: Optional[dict] = None
    custom_message: Optional[str] = None


class TranscriptRecord(BaseModel):
    """A completed call transcript for logging to Sheets."""
    call_sid: str
    call_type: CallType
    from_number: str
    to_number: str
    started_at: str
    duration_seconds: int = 0
    transcript: str = ""
    appointment_booked: bool = False
    appointment_data: Optional[dict] = None
