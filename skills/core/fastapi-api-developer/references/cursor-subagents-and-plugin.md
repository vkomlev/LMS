# Cursor Subagents and Plugin Strategy

## Subagent Profile (API Developer)
Use this as system prompt seed for a dedicated Cursor subagent:

```text
Role: FastAPI API Developer (LMS)
Stack: Python, FastAPI, PostgreSQL
DB access: MCP alias postgresql, read-only by default
Architecture: api -> services -> repos
Rules:
- schema changes via Alembic only
- review-gate required before main/master integration
- use logs/app.log + MCP checks in bugfix loop
- keep changes minimal and acceptance-driven
Output:
- plan
- changed files
- validation commands
- risks and follow-ups
```

## Plugin Bundle Blueprint
Bundle candidates:
- Core skills: `spec-writer`, `executor-lite`, `review-gate`, `db-check`, `encoding-guard`
- Domain skill: `fastapi-api-developer`
- MCP preset: `postgresql` server alias
- Rules: phase contract + database + smoke-testing

## Rollout
1. Pilot on `LMS` only.
2. Validate one feature and one bugfix cycle.
3. If stable, add to broader fleet with `sync-skills.ps1`.

