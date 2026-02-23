# Directive: Knowledge Base Management

## Goal
Maintain the salon knowledge base that the AI agent uses to answer questions and validate appointment data.

## KB File
`knowledge_base/salon_info.json`

## Structure

The KB is a single JSON file with these sections:

| Section | Contents |
|---------|----------|
| `salon` | Name, tagline, phone, email, website |
| `locations` | Address, phone, business hours per location |
| `services` | Grouped by category, each with name, duration, price |
| `technicians` | Name, title, specialties, available days |
| `policies` | Cancellation, late arrival, deposits, etc. |
| `faq` | Common questions and answers |

## How the Agent Uses It

1. **System prompt** — Full KB summary is injected into Gemini's context
2. **Service matching** — Customer requests are fuzzy-matched to service names
3. **Technician routing** — Agent suggests technicians based on requested service
4. **Pricing info** — Agent quotes prices from the KB (never guesses)
5. **Policy answers** — Cancellation, payment, parking questions answered from KB

## Tools

| Script | Purpose |
|--------|---------|
| `execution/manage_kb.py validate` | Validate KB structure and cross-references |
| `execution/manage_kb.py list-technicians` | Show all technicians |
| `execution/manage_kb.py list-services` | Show all services |
| `execution/manage_kb.py add-technician` | Add a new technician |
| `POST /api/knowledge-base/reload` | Hot-reload KB without restart |

## Updating the KB

1. Edit `knowledge_base/salon_info.json` directly (it's human-readable JSON)
2. Run `python execution/manage_kb.py validate` to check for errors
3. Hit `POST /api/knowledge-base/reload` to apply changes live
4. No server restart needed

## Rules

- Technician specialties MUST match exact service names from the services list
- Every service must have `name`, `duration_minutes`, and `price`
- Available days are lowercase: `monday`, `tuesday`, etc.
- Prices can be ranges (`$45 - $75`) or fixed (`$25`)
