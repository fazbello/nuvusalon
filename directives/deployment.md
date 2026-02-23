# Directive: Railway Deployment

## Goal
Deploy the NuvuSalon Voice Agent to Railway so it's accessible as a public URL for Twilio webhooks.

## Prerequisites

1. **Railway account** at https://railway.app
2. **Twilio account** with a phone number
3. **Google Cloud project** with Calendar API + Sheets API enabled, service account created
4. **SendGrid account** with verified sender
5. **Gemini API key** from Google AI Studio

## Step-by-Step

### 1. Push to GitHub
```bash
git push origin main
```

### 2. Create Railway Project
- New Project → Deploy from GitHub repo
- Railway auto-detects Python (Nixpacks)
- Build runs `pip install -r requirements.txt`
- Start command is in `Procfile`

### 3. Set Environment Variables in Railway Dashboard

| Variable | Example | Required |
|----------|---------|----------|
| `TWILIO_ACCOUNT_SID` | `ACxxxxxxx` | Yes |
| `TWILIO_AUTH_TOKEN` | `xxxxxxx` | Yes |
| `TWILIO_PHONE_NUMBER` | `+11234567890` | Yes |
| `GEMINI_API_KEY` | `AIzaSyXXX` | Yes |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | `{"type":"service_account",...}` | Yes |
| `GOOGLE_SHEET_ID` | `1BxiMVs0XRA5nFMdK...` | Yes |
| `GOOGLE_CALENDAR_ID` | `primary` or `calendar@group.calendar.google.com` | Yes |
| `SENDGRID_API_KEY` | `SG.xxxxx` | Yes |
| `FROM_EMAIL` | `bookings@nuvusalon.com` | Yes |
| `SALON_NOTIFICATION_EMAIL` | `owner@nuvusalon.com` | Yes |
| `SALON_NAME` | `Nuvu Salon & Spa` | Yes |
| `SALON_TIMEZONE` | `America/New_York` | Yes |
| `BASE_URL` | `https://myapp.up.railway.app` | Yes |
| `DEBUG` | `false` | No |

### 4. Get Your Public URL
- Railway assigns a URL like `https://nuvusalon-production.up.railway.app`
- Set this as `BASE_URL` in Railway env vars

### 5. Configure Twilio Webhooks
- Go to Twilio Console → Phone Numbers → Your Number
- **Voice & Fax** section:
  - "A call comes in": Webhook → `https://<your-railway-url>/voice/inbound` (POST)
  - "Call status changes": `https://<your-railway-url>/voice/status` (POST)

### 6. Verify Deployment
```bash
# Health check
curl https://<your-railway-url>/health

# Test services API
curl https://<your-railway-url>/api/services

# Test outbound call
curl -X POST https://<your-railway-url>/api/outbound-call \
  -H "Content-Type: application/json" \
  -d '{"phone_number": "+1YOUR_NUMBER", "purpose": "reminder"}'
```

## Google Service Account Setup

1. Go to Google Cloud Console → IAM & Admin → Service Accounts
2. Create a service account
3. Download JSON key
4. Copy the entire JSON content as the value of `GOOGLE_SERVICE_ACCOUNT_JSON`
5. Share your Google Sheet with the service account email
6. Share your Google Calendar with the service account email (give "Make changes to events")

## Monitoring

- Railway logs: `railway logs`
- Health endpoint: `GET /health`
- Active calls: `GET /api/status`
- All transcripts: Check the Transcripts tab in Google Sheets

## Learnings

- Railway auto-assigns `PORT` — the app reads it via uvicorn
- Service account JSON must be a single-line string in Railway env vars
- SendGrid requires sender verification before emails will deliver
- Twilio trial accounts can only call verified numbers
