# Cursor Agent Error Loop

## Goal
Capture significant Cursor-agent mistakes during review and convert them into preventive improvements in skills/rules/workflows.

## What To Log
Log when at least one condition is true:
- wrong implementation that violates task scope or contract
- outdated library/framework API usage
- architecture boundary violation
- unsafe DB/schema operation pattern
- missing/incorrect tests causing regression risk
- repeated copy-paste/DRY violation

## Where To Log
1. Project-specific register (preferred):
- `<project>/docs/ai/ERRORS.md`

2. If project register is unavailable:
- `d:/Work/IDE_booster/Docs/ai/ERRORS.md`

## Minimum Entry Fields
- Date
- Project
- Context
- Symptom
- Root Cause
- Class
- Severity
- Detection Method
- Fix
- Prevention Action (skill/rule/workflow update)
- Status

## Required Follow-Up
For each logged Cursor-agent mistake, add at least one preventive action:
- update skill instructions,
- tighten project rules,
- add validation/test command,
- add reusable helper/template to remove ambiguity.

