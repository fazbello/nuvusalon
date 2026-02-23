#!/usr/bin/env python3
"""
Execution script: Initialize Google Sheets tabs.

Run once to create the Appointments, Transcripts, and Customers tabs
with proper headers in your Google Sheet.

Usage:
    python execution/setup_google_sheets.py
"""

import sys
from pathlib import Path

# Ensure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.integrations.google_sheets import setup_spreadsheet


def main():
    print("Setting up Google Sheets...")
    try:
        url = setup_spreadsheet()
        print(f"Spreadsheet ready: {url}")
        print("Tabs created: Appointments, Transcripts, Customers")
    except Exception as exc:
        print(f"ERROR: {exc}")
        print()
        print("Make sure you have:")
        print("  1. GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS set in .env")
        print("  2. GOOGLE_SHEET_ID set in .env (the spreadsheet must exist)")
        print("  3. The service account has Editor access to the spreadsheet")
        sys.exit(1)


if __name__ == "__main__":
    main()
