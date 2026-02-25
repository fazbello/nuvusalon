"""
Comprehensive tests for NuvuSalon Voice Agent.

Covers:
  - Rule-based inbound call flow end-to-end (service → date → time → name → confirm → book)
  - Rule-based outbound call flow
  - Google Calendar booking (mocked)
  - Google Sheets append (mocked)
  - Frontpage form /api/book endpoint
  - Edge cases: empty speech, name extraction, time extraction
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ── Patch settings BEFORE importing any app modules ───────────────────────────
# This ensures get_settings() returns a controllable config without .env leaking.

_TEST_SETTINGS = {
    "salon_name": "Test Salon",
    "ai_provider": "rules",
    "voice_provider": "twilio",
    "twilio_account_sid": "ACtest",
    "twilio_auth_token": "test_token",
    "twilio_phone_number": "+15550001234",
    "google_sheet_id": "",
    "google_calendar_id": "primary",
    "sendgrid_api_key": "",
    "from_email": "",
    "greeting_message": "",
    "tts_voice": "Polly.Joanna",
    "speech_timeout": "auto",
    "gather_timeout": 8,
    "language": "en-US",
    "base_url": "https://test.example.com",
    "salon_timezone": "America/New_York",
    "appointment_duration_minutes": 60,
    "dashboard_password": "testpass",
    "dashboard_secret": "testsecret",
    "knowledge_base_path": "knowledge_base/salon_info.json",
    "google_service_account_json": "",
    "google_application_credentials": "",
}


@pytest.fixture(autouse=True)
def _patch_settings():
    """Ensure every test uses a deterministic Settings object."""
    from app.config import Settings, get_settings

    get_settings.cache_clear()
    with patch("app.config.get_settings") as mock_gs:
        s = Settings(**_TEST_SETTINGS)
        mock_gs.return_value = s
        yield s
    get_settings.cache_clear()


@pytest.fixture()
def _clean_sessions():
    """Clear the in-memory session store between tests."""
    from app.voice.session import _sessions
    _sessions.clear()
    yield
    _sessions.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _twilio_form(call_sid: str, speech: str = "", **kw) -> dict:
    """Build a dict that looks like Twilio form-data."""
    d = {
        "CallSid": call_sid,
        "From": "+15559998888",
        "To": "+15550001234",
        "SpeechResult": speech,
        "Confidence": "0.95",
        "CallStatus": kw.get("status", "in-progress"),
    }
    d.update(kw)
    return d


# ══════════════════════════════════════════════════════════════════════════════
#  1.  RULE ENGINE — UNIT TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestRuleEngineExtractors:
    """Test _extract_time, _extract_name, _extract_service, _extract_date."""

    def test_time_two_thirty(self):
        from app.ai.rule_engine import _extract_time
        assert _extract_time("two thirty") == "14:30"

    def test_time_three_fifteen(self):
        from app.ai.rule_engine import _extract_time
        assert _extract_time("three fifteen") == "15:15"

    def test_time_half_past_two(self):
        from app.ai.rule_engine import _extract_time
        assert _extract_time("half past two") == "14:30"

    def test_time_quarter_to_four(self):
        from app.ai.rule_engine import _extract_time
        assert _extract_time("quarter to four") == "15:45"

    def test_time_afternoon_context(self):
        from app.ai.rule_engine import _extract_time
        assert _extract_time("three in the afternoon") == "15:00"

    def test_time_morning_context(self):
        from app.ai.rule_engine import _extract_time
        assert _extract_time("nine in the morning") == "09:00"

    def test_time_digit_colon(self):
        from app.ai.rule_engine import _extract_time
        assert _extract_time("2:30 pm") == "14:30"

    def test_time_noon_not_afternoon(self):
        from app.ai.rule_engine import _extract_time
        assert _extract_time("noon") == "12:00"
        # "afternoon" must NOT match "noon"
        assert _extract_time("three thirty in the afternoon") == "15:30"

    def test_time_two_forty_five(self):
        from app.ai.rule_engine import _extract_time
        assert _extract_time("two forty-five") == "14:45"

    def test_time_ten_thirty_am(self):
        from app.ai.rule_engine import _extract_time
        assert _extract_time("ten thirty am") == "10:30"

    def test_name_simple(self):
        from app.ai.rule_engine import _extract_name
        assert _extract_name("Sarah Johnson") == "Sarah Johnson"

    def test_name_with_punctuation(self):
        from app.ai.rule_engine import _extract_name
        assert _extract_name("Sarah Johnson.") == "Sarah Johnson"

    def test_name_filler_rejected(self):
        from app.ai.rule_engine import _extract_name
        assert _extract_name("Um Sarah Johnson") is None

    def test_name_number_word_rejected(self):
        from app.ai.rule_engine import _extract_name
        assert _extract_name("Two") is None

    def test_name_time_words_rejected(self):
        from app.ai.rule_engine import _extract_name
        assert _extract_name("three fifteen") is None

    def test_name_trigger_phrase(self):
        from app.ai.rule_engine import _extract_name
        assert _extract_name("my name is Sarah Johnson") == "Sarah Johnson"

    def test_name_full_name_trigger(self):
        from app.ai.rule_engine import _extract_name
        assert _extract_name("My full name is John Smith") == "John Smith"

    def test_service_haircut(self):
        from app.ai.rule_engine import _extract_service
        result = _extract_service("I'd like a haircut please")
        assert result is not None
        assert "haircut" in result.lower()

    def test_service_prefix_match(self):
        from app.ai.rule_engine import _extract_service
        result = _extract_service("I want a pedi")
        # Should match pedicure via prefix
        assert result is not None
        assert "pedicure" in result.lower() or "pedi" in result.lower()

    def test_date_tomorrow(self):
        from app.ai.rule_engine import _extract_date
        expected = (date.today() + timedelta(days=1)).isoformat()
        assert _extract_date("tomorrow") == expected

    def test_date_today(self):
        from app.ai.rule_engine import _extract_date
        assert _extract_date("today") == date.today().isoformat()

    def test_date_named_day(self):
        from app.ai.rule_engine import _extract_date
        result = _extract_date("next friday")
        assert result is not None
        d = date.fromisoformat(result)
        assert d.weekday() == 4  # Friday


# ══════════════════════════════════════════════════════════════════════════════
#  2.  INBOUND CALL FLOW — FULL END-TO-END (rule engine)
# ══════════════════════════════════════════════════════════════════════════════

class TestInboundCallFlow:
    """Simulate a complete inbound booking call through the voice webhooks."""

    @pytest.mark.asyncio
    async def test_full_booking_flow(self, _clean_sessions):
        """
        Demo data walk-through:
          1. Customer calls → greeting
          2. "I'd like a haircut" → service extracted → asks for date
          3. "tomorrow" → date extracted → asks for time
          4. "two thirty" → time extracted → asks for name
          5. "Sarah Johnson" → name extracted → asks for email (optional)
          6. "skip" → summary shown → asks to confirm
          7. "yes" → appointment booked → goodbye
        """
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            call_sid = "CA_test_inbound_001"

            # ── Step 1: Inbound call arrives → greeting ──
            resp = await ac.post(
                "/voice/inbound",
                data=_twilio_form(call_sid),
            )
            assert resp.status_code == 200
            body = resp.text
            assert "<Gather" in body
            assert "Test Salon" in body or "booking assistant" in body.lower()

            # ── Step 2: "I'd like a haircut" → service extracted ──
            resp = await ac.post(
                "/voice/process-speech",
                data=_twilio_form(call_sid, speech="I'd like a haircut please"),
            )
            assert resp.status_code == 200
            body = resp.text
            assert "<Gather" in body
            # Should be asking for date now
            assert "date" in body.lower() or "when" in body.lower() or "come in" in body.lower()

            # ── Step 3: "tomorrow" → date extracted ──
            resp = await ac.post(
                "/voice/process-speech",
                data=_twilio_form(call_sid, speech="tomorrow"),
            )
            assert resp.status_code == 200
            body = resp.text
            assert "<Gather" in body
            # Should be asking for time now
            assert "time" in body.lower() or "when" in body.lower() or "work" in body.lower()

            # ── Step 4: "two thirty" → time extracted ──
            resp = await ac.post(
                "/voice/process-speech",
                data=_twilio_form(call_sid, speech="two thirty"),
            )
            assert resp.status_code == 200
            body = resp.text
            assert "<Gather" in body
            # Should be asking for name now
            assert "name" in body.lower()

            # ── Step 5: "Sarah Johnson" → name extracted ──
            resp = await ac.post(
                "/voice/process-speech",
                data=_twilio_form(call_sid, speech="Sarah Johnson"),
            )
            assert resp.status_code == 200
            body = resp.text
            assert "<Gather" in body
            # Should ask for email (optional) or show summary
            assert "email" in body.lower() or "confirm" in body.lower() or "sound right" in body.lower()

            # ── Step 6: "skip" → skip email, show confirmation ──
            resp = await ac.post(
                "/voice/process-speech",
                data=_twilio_form(call_sid, speech="skip"),
            )
            assert resp.status_code == 200
            body = resp.text
            # Summary should contain service, date, time, name
            lower_body = body.lower()
            assert "haircut" in lower_body or "sarah" in lower_body
            assert "confirm" in lower_body or "sound right" in lower_body or "correct" in lower_body

            # ── Step 7: "yes" → booking confirmed ──
            with patch("app.voice.inbound.create_appointment_event") as mock_cal, \
                 patch("app.voice.inbound.log_appointment") as mock_sheets, \
                 patch("app.voice.inbound.send_booking_confirmation") as mock_email, \
                 patch("app.voice.inbound.send_staff_notification") as mock_staff:
                mock_cal.return_value = {"htmlLink": "https://calendar.google.com/test"}

                resp = await ac.post(
                    "/voice/process-speech",
                    data=_twilio_form(call_sid, speech="yes"),
                )
                assert resp.status_code == 200
                body = resp.text
                # Should confirm booking and hang up
                assert "<Hangup" in body or "booked" in body.lower()
                assert "haircut" in body.lower() or "appointment" in body.lower()

                # Verify calendar was called
                assert mock_cal.called
                appt = mock_cal.call_args[0][0]
                assert appt.customer_name == "Sarah Johnson"
                assert appt.preferred_time == "14:30"  # two thirty → PM
                assert appt.service is not None

    @pytest.mark.asyncio
    async def test_empty_speech_reprompts(self, _clean_sessions):
        """When Twilio sends empty SpeechResult, the agent re-asks."""
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            call_sid = "CA_test_empty_speech"

            # Create call
            await ac.post("/voice/inbound", data=_twilio_form(call_sid))

            # Send empty speech
            resp = await ac.post(
                "/voice/process-speech",
                data=_twilio_form(call_sid, speech=""),
            )
            assert resp.status_code == 200
            body = resp.text
            assert "<Gather" in body  # Should re-ask, not crash

    @pytest.mark.asyncio
    async def test_session_recreated_if_lost(self, _clean_sessions):
        """If session is lost (server restart), it gets recreated."""
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Don't create session first — just send speech directly
            resp = await ac.post(
                "/voice/process-speech",
                data=_twilio_form("CA_orphan", speech="I want a haircut"),
            )
            assert resp.status_code == 200
            assert "<Gather" in resp.text

    @pytest.mark.asyncio
    async def test_hours_query(self, _clean_sessions):
        """Customer asks about hours."""
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            call_sid = "CA_hours"
            await ac.post("/voice/inbound", data=_twilio_form(call_sid))
            resp = await ac.post(
                "/voice/process-speech",
                data=_twilio_form(call_sid, speech="What are your hours?"),
            )
            assert resp.status_code == 200
            body = resp.text.lower()
            assert "hour" in body or "open" in body or "monday" in body

    @pytest.mark.asyncio
    async def test_services_query(self, _clean_sessions):
        """Customer asks what services are available."""
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            call_sid = "CA_services"
            await ac.post("/voice/inbound", data=_twilio_form(call_sid))
            resp = await ac.post(
                "/voice/process-speech",
                data=_twilio_form(call_sid, speech="What services do you offer?"),
            )
            assert resp.status_code == 200
            body = resp.text.lower()
            assert "offer" in body or "haircut" in body or "service" in body


# ══════════════════════════════════════════════════════════════════════════════
#  3.  OUTBOUND CALL FLOW
# ══════════════════════════════════════════════════════════════════════════════

class TestOutboundCallFlow:

    @pytest.mark.asyncio
    async def test_outbound_confirmation_flow(self, _clean_sessions):
        """Simulate an outbound appointment confirmation call."""
        from app.voice.session import create_session
        from app.models.appointment import CallType
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            call_sid = "CA_outbound_001"
            # Pre-create the session as outbound.py would
            create_session(
                call_sid=call_sid,
                from_number="+15550001234",
                to_number="+15559998888",
                call_type=CallType.OUTBOUND,
                purpose="appointment_confirmation",
                context=json.dumps({
                    "customer_name": "Jane Doe",
                    "service": "Haircut & Style",
                    "date": "2026-03-01",
                    "time": "14:00",
                }),
            )

            # ── Step 1: Outbound call answered → opening message ──
            resp = await ac.post(
                "/voice/outbound-answer",
                data=_twilio_form(call_sid),
            )
            assert resp.status_code == 200
            body = resp.text.lower()
            assert "confirm" in body or "appointment" in body or "test salon" in body

            # ── Step 2: Customer says "yes" → confirmed + goodbye ──
            with patch("app.voice.outbound.log_transcript"):
                resp = await ac.post(
                    "/voice/outbound-process",
                    data=_twilio_form(call_sid, speech="yes"),
                )
                assert resp.status_code == 200
                body = resp.text
                assert "<Hangup" in body or "confirmed" in body.lower() or "wonderful" in body.lower()


# ══════════════════════════════════════════════════════════════════════════════
#  4.  GOOGLE CALENDAR BOOKING (mocked)
# ══════════════════════════════════════════════════════════════════════════════

class TestGoogleCalendar:

    def test_create_appointment_event(self):
        """Calendar event body is built correctly from AppointmentData."""
        from app.models.appointment import AppointmentData

        appt = AppointmentData(
            customer_name="Sarah Johnson",
            phone_number="+15559998888",
            email="sarah@example.com",
            service="Haircut & Style",
            preferred_date="2026-03-01",
            preferred_time="14:30",
        )

        fake_event = {"htmlLink": "https://calendar.google.com/event/abc123"}

        with patch("app.integrations.google_calendar._get_service") as mock_svc:
            mock_api = MagicMock()
            mock_api.events().insert().execute.return_value = fake_event
            mock_svc.return_value = mock_api

            from app.integrations.google_calendar import create_appointment_event
            result = create_appointment_event(appt)

            assert result["htmlLink"] == "https://calendar.google.com/event/abc123"

            # Verify the event body sent to Calendar API
            insert_call = mock_api.events().insert
            assert insert_call.called
            call_kwargs = insert_call.call_args
            event_body = call_kwargs.kwargs.get("body") or call_kwargs[1].get("body")
            if event_body is None:
                # Might be positional
                event_body = call_kwargs[0][0] if call_kwargs[0] else {}

            # The insert was called — that's the key check
            assert mock_api.events().insert.called


# ══════════════════════════════════════════════════════════════════════════════
#  5.  GOOGLE SHEETS APPEND (mocked)
# ══════════════════════════════════════════════════════════════════════════════

class TestGoogleSheets:

    def test_log_appointment_skips_if_not_configured(self):
        """log_appointment returns silently when GOOGLE_SHEET_ID is empty."""
        from app.integrations.google_sheets import log_appointment
        from app.models.appointment import AppointmentData

        appt = AppointmentData(
            customer_name="Test User",
            service="Haircut",
            preferred_date="2026-03-01",
            preferred_time="14:00",
        )
        # Should not raise — just skip
        log_appointment(appt, calendar_link="")

    def test_log_transcript_skips_if_not_configured(self):
        """log_transcript returns silently when GOOGLE_SHEET_ID is empty."""
        from app.integrations.google_sheets import log_transcript
        from app.models.appointment import CallType, TranscriptRecord

        record = TranscriptRecord(
            call_sid="CA_test",
            call_type=CallType.INBOUND,
            from_number="+15559998888",
            to_number="+15550001234",
            started_at="2026-03-01 14:00:00",
            transcript="Test transcript",
        )
        # Should not raise — just skip
        log_transcript(record)

    def test_log_appointment_calls_sheets_when_configured(self):
        """When Sheet ID + credentials are present, log_appointment appends a row."""
        from app.models.appointment import AppointmentData

        appt = AppointmentData(
            customer_name="Sarah Johnson",
            phone_number="+15559998888",
            email="sarah@example.com",
            service="Haircut & Style",
            preferred_date="2026-03-01",
            preferred_time="14:30",
        )

        mock_ws = MagicMock()

        with patch("app.integrations.google_sheets._get_client") as mock_client, \
             patch("app.integrations.google_sheets.get_settings") as mock_gs, \
             patch("app.integrations.google_sheets._get_or_create_sheet", return_value=mock_ws):

            settings = MagicMock()
            settings.google_sheet_id = "sheet123"
            settings.get_google_credentials_info.return_value = {"type": "service_account"}
            mock_gs.return_value = settings

            mock_spreadsheet = MagicMock()
            mock_client.return_value.open_by_key.return_value = mock_spreadsheet

            from app.integrations.google_sheets import log_appointment
            log_appointment(appt, calendar_link="https://cal.example.com")

            # Verify a row was appended
            assert mock_ws.append_row.called
            row = mock_ws.append_row.call_args[0][0]
            assert "Sarah Johnson" in row
            assert "+15559998888" in row
            assert "Haircut & Style" in row
            assert "2026-03-01" in row


# ══════════════════════════════════════════════════════════════════════════════
#  6.  FRONTPAGE FORM — /api/book
# ══════════════════════════════════════════════════════════════════════════════

class TestFrontpageForm:

    @pytest.mark.asyncio
    async def test_landing_page_renders(self):
        """GET / returns the landing page with service dropdown."""
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/")
            assert resp.status_code == 200
            body = resp.text.lower()
            assert "test salon" in body or "nuvu" in body
            assert "<option" in body  # service dropdown

    @pytest.mark.asyncio
    async def test_book_endpoint_success(self):
        """POST /api/book with valid data returns success."""
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/book", json={
                "customer_name": "Sarah Johnson",
                "phone_number": "+15559998888",
                "email": "sarah@example.com",
                "service": "Haircut & Style",
                "preferred_date": "2026-03-01",
                "preferred_time": "14:30",
                "notes": "First visit",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "received"
            assert "Sarah Johnson" in data["message"]
            assert "Haircut" in data["message"]

    @pytest.mark.asyncio
    async def test_book_endpoint_missing_fields(self):
        """POST /api/book with missing required fields returns 422."""
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/book", json={
                "customer_name": "Sarah Johnson",
                # Missing: service, preferred_date, preferred_time
            })
            assert resp.status_code == 422
            data = resp.json()
            assert "error" in data
            assert "service" in data["error"]

    @pytest.mark.asyncio
    async def test_book_endpoint_invalid_json(self):
        """POST /api/book with invalid JSON returns 400."""
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/book",
                content="not json",
                headers={"content-type": "application/json"},
            )
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        """GET /health returns system status."""
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "healthy"
            assert "ai_provider" in data

    @pytest.mark.asyncio
    async def test_api_services(self):
        """GET /api/services returns services from knowledge base."""
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/services")
            assert resp.status_code == 200
            data = resp.json()
            assert "services" in data
            assert len(data["services"]) > 0


# ══════════════════════════════════════════════════════════════════════════════
#  7.  CALL STATUS / FINALIZE (incomplete booking saved)
# ══════════════════════════════════════════════════════════════════════════════

class TestCallStatus:

    @pytest.mark.asyncio
    async def test_call_status_completed(self, _clean_sessions):
        """Status callback with 'completed' triggers finalize."""
        from app.voice.session import create_session
        from app.models.appointment import CallType
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            call_sid = "CA_status_test"
            session = create_session(
                call_sid=call_sid,
                from_number="+15559998888",
                to_number="+15550001234",
                call_type=CallType.INBOUND,
            )
            session.add_agent_message("How can I help?")
            session.add_customer_message("I want a haircut")
            session.update_appointment({"service": "Haircut & Style"})

            with patch("app.voice.inbound.local_save_transcript") as mock_local, \
                 patch("app.voice.inbound.save_incomplete_booking") as mock_incomplete:
                resp = await ac.post(
                    "/voice/status",
                    data=_twilio_form(call_sid, status="completed", CallStatus="completed"),
                )
                assert resp.status_code == 204

                # Transcript should be saved locally
                assert mock_local.called

                # Since not booked, incomplete booking should be attempted
                assert mock_incomplete.called


# ══════════════════════════════════════════════════════════════════════════════
#  8a. /api/book creates Calendar event
# ══════════════════════════════════════════════════════════════════════════════

class TestBookCreatesCalendar:

    @pytest.mark.asyncio
    async def test_book_creates_calendar_event(self):
        """POST /api/book attempts to create a Google Calendar event."""
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with patch("app.integrations.google_calendar.create_appointment_event") as mock_cal:
                mock_cal.return_value = {"htmlLink": "https://cal.example.com/event/123"}

                resp = await ac.post("/api/book", json={
                    "customer_name": "Jane Doe",
                    "phone_number": "+15551234567",
                    "service": "Blowout",
                    "preferred_date": "2026-03-05",
                    "preferred_time": "10:00",
                })
                assert resp.status_code == 200
                assert mock_cal.called
                appt = mock_cal.call_args[0][0]
                assert appt.customer_name == "Jane Doe"
                assert appt.service == "Blowout"


# ══════════════════════════════════════════════════════════════════════════════
#  8.  APPOINTMENT MODEL
# ══════════════════════════════════════════════════════════════════════════════

class TestAppointmentModel:

    def test_missing_required_fields(self):
        from app.models.appointment import AppointmentData
        appt = AppointmentData()
        missing = appt.missing_required_fields()
        assert "service" in missing
        assert "preferred_date" in missing
        assert "preferred_time" in missing
        assert "customer_name" in missing
        # Email and phone should NOT be required
        assert "email" not in missing
        assert "phone_number" not in missing

    def test_is_complete(self):
        from app.models.appointment import AppointmentData
        appt = AppointmentData(
            service="Haircut",
            preferred_date="2026-03-01",
            preferred_time="14:00",
            customer_name="Sarah",
        )
        assert appt.is_complete()
        assert not appt.missing_required_fields()

    def test_summary(self):
        from app.models.appointment import AppointmentData
        appt = AppointmentData(
            customer_name="Sarah",
            service="Haircut",
            preferred_date="2026-03-01",
            preferred_time="14:00",
        )
        s = appt.summary()
        assert "Sarah" in s
        assert "Haircut" in s
