---
name: executor-lite
description: Execute low-risk, routine implementation tasks with strict scope control and minimal token usage. Use for deterministic edits, repetitive transformations, formatting, boilerplate, and simple test scaffolding.
---

# Executor Lite

## Workflow
1. Confirm scope in one sentence.
2. Identify exact files and minimal edits.
3. Implement smallest viable change.
4. Run lightweight validation (lint/smoke/unit subset).
5. Summarize changed files and validation result.

## Use Cases
- repetitive refactors with known pattern
- small bug fixes with clear root cause
- docs and config alignment
- test skeleton generation

## Stop Conditions
- ambiguous requirements
- cross-module side effects
- failing validation without obvious fix

Escalate to a higher reasoning skill/model when stop conditions occur.
