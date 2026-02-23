# Directive: Appointment Booking Flow

## Goal
Handle a customer phone call end-to-end: greet, collect appointment details, book, confirm, and notify.

## Trigger
- **Inbound call** arrives on the salon Twilio number
- **Outbound call** initiated via `/api/outbound-call`

## Inbound Call Flow

### Step 1: Greeting
- Twilio webhook hits `POST /voice/inbound`
- System creates a `CallSession` keyed by `CallSid`
- Responds with a warm greeting + `<Gather speech>` to listen

### Step 2: Conversation Loop
- Customer speech → Twilio transcribes → hits `POST /voice/process-speech`
- Session history + collected data → Gemini agent
- Gemini returns: spoken message, any newly extracted fields, next action
- Loop continues until all required fields are collected

### Step 3: Confirmation
- When Gemini detects all required fields → sets action to `confirm`
- Agent reads back all details and asks customer to confirm
- Customer says yes → action becomes `book`

### Step 4: Booking
- Create Google Calendar event (sends invite to customer email)
- Log appointment to Google Sheets (Appointments tab)
- Upsert customer record in Sheets (Customers tab)
- Send confirmation email to customer via SendGrid
- Send staff notification email to salon
- Say confirmation message → hangup

### Step 5: Transcript Logging
- On call end (status callback), full transcript is logged to Sheets (Transcripts tab)
- Session is cleaned up from memory

## Required Data Fields

| Field | Description | Required |
|-------|-------------|----------|
| `customer_name` | Full name | Yes |
| `phone_number` | Auto-captured from caller ID | Yes |
| `email` | For confirmation email | Yes |
| `service` | Must match a KB service | Yes |
| `preferred_date` | YYYY-MM-DD | Yes |
| `preferred_time` | HH:MM (24h) | Yes |
| `location` | Salon location (if multi) | No |
| `technician` | Preferred staff member | No |
| `notes` | Special requests | No |

## Outbound Call Flow

1. `POST /api/outbound-call` with phone, purpose, and optional details
2. Twilio initiates call → customer answers → hits `/voice/outbound-answer`
3. Gemini generates opening based on purpose (confirm, remind, follow-up)
4. Conversation loop same as inbound
5. Transcript logged on completion

## Edge Cases

- **No speech detected**: Prompt retry, then offer callback after 3 failures
- **Session lost** (server restart): Recreate session from CallSid, apologize for reset
- **Calendar conflict**: Gemini should suggest alternative times
- **Customer wants human**: Action `transfer` → dial salon number
- **Gemini JSON parse failure**: Fallback to raw text as spoken message

## Tools / Scripts

| Script | Purpose |
|--------|---------|
| `app/voice/inbound.py` | Inbound call handling |
| `app/voice/outbound.py` | Outbound call initiation |
| `app/ai/gemini_agent.py` | Conversation intelligence |
| `app/integrations/google_calendar.py` | Calendar booking |
| `app/integrations/google_sheets.py` | Data logging |
| `app/integrations/email_sender.py` | Email notifications |

## Learnings
- Twilio `<Gather>` with `speechTimeout="auto"` works best for natural speech
- Gemini's JSON mode occasionally wraps in markdown fences — parser handles this
- SendGrid free tier: 100 emails/day — sufficient for most salons
