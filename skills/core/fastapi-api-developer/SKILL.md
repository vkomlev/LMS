---
name: fastapi-api-developer
description: "Implement and debug FastAPI backend changes with PostgreSQL MCP-aware analysis, schema-aware SQL checks, and log-driven diagnosis. Use for API feature delivery, bugfixes, DB-impacting changes, and smoke-debug loops in LMS-style Python services."
---

# FastAPI API Developer

## Workflow
1. Read project AI contract and overrides from [references/lms-operating-context.md](references/lms-operating-context.md).
2. Restate objective, scope, and acceptance criteria.
3. Inspect current API/service/repo code path before editing.
4. For DB-aware work, run read-only MCP schema/data checks from [references/mcp-postgres-playbook.md](references/mcp-postgres-playbook.md).
5. Implement minimal scoped changes (`api` -> `services` -> `repos` layering).
6. Run smoke/test checks and analyze logs using `scripts/log_triage.py` and [references/log-debug-playbook.md](references/log-debug-playbook.md).
7. Produce review-ready artifacts with PASS/FAIL prerequisites.

## Input Contract
- `Objective`
- `Affected Endpoints/Modules`
- `Data Impact` (`none|read|schema|write`)
- `Acceptance Criteria`

## Output Contract
- `Implementation Plan`
- `Code Changes`
- `DB Findings (MCP)`
- `Log Diagnosis`
- `Validation Results`
- `Risks and Follow-ups`

## Optional Packaging For Cursor
Use [references/cursor-subagents-and-plugin.md](references/cursor-subagents-and-plugin.md) to package this skill as:
- dedicated subagent prompt profile for API development;
- plugin bundle entry with MCP + rules + core skills.

## Quality Rules
- Enforce read-only MCP by default; write operations require explicit task approval.
- For schema changes, require Alembic migration + rollback note.
- Do not close bugfix loop without log evidence and endpoint smoke confirmation.
- Keep changes minimal and mapped to acceptance criteria.
