# Directives

This directory contains SOPs (Standard Operating Procedures) written in Markdown.

Each directive defines:
- **Goal** — what the task accomplishes
- **Inputs** — what information or files are required
- **Tools/Scripts** — which `execution/` scripts to call and in what order
- **Outputs** — what gets produced (deliverable or intermediate)
- **Edge Cases** — known failure modes, API limits, timing expectations

## How to Use

The orchestration layer (AI agent) reads the relevant directive before starting any task. Directives are living documents — update them when you discover new constraints or better approaches.

## Naming Convention

Use descriptive snake_case filenames:
```
directives/
  scrape_website.md
  generate_report.md
  sync_google_sheet.md
```
