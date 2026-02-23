# Execution Scripts

This directory contains deterministic Python scripts — the workhorses of the system.

Each script:
- Does one thing well
- Reads config from environment variables (`.env`)
- Is testable and fast
- Is well-commented

## How to Use

Scripts are called by the orchestration layer (AI agent) after reading the relevant directive. Never run scripts that use paid API credits without confirming with the user first.

## Naming Convention

Use descriptive snake_case filenames that match their directive:
```
execution/
  scrape_single_site.py
  export_to_sheet.py
  generate_slides.py
```

## Environment Variables

All secrets and config live in `.env` at the repo root. Use `python-dotenv` to load them:

```python
from dotenv import load_dotenv
import os

load_dotenv()
API_KEY = os.getenv("API_KEY")
```
