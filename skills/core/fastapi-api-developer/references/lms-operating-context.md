# LMS Operating Context

## Source of Truth
- `d:/Work/LMS/docs/ai/AGENTS.md`
- `d:/Work/LMS/docs/ai/PROJECT_OVERRIDES.md`
- `d:/Work/LMS/docs/ai/WORKFLOWS/feature.md`
- `d:/Work/LMS/docs/ai/WORKFLOWS/bugfix.md`
- `d:/Work/LMS/docs/ai/WORKFLOWS/db-change.md`
- `d:/Work/LMS/.cursor/mcp.json`
- `d:/Work/LMS/.cursor/README-MCP.md`
- `d:/Work/LMS/.cursor/rules/*.mdc`

## Key Constraints
- Stack: Python + FastAPI + PostgreSQL.
- Layering rule: `api -> services -> repos`.
- DB schema changes only via Alembic migrations.
- MCP alias: `postgresql`.
- DB interactions are read-only by default for analysis/debug.
- `review-gate` required before integration to `main/master`.

## Validation Baseline
- Relevant tests (`pytest tests/...`).
- Smoke checks for health + changed endpoints.
- Log validation from `logs/app.log`.
- DB checks through MCP for data/state verification.

