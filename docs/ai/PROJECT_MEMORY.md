# Project Memory

Project: LMS API
Path: `d:\Work\LMS`
Created: 2026-05-26
Profile updated: 2026-05-26

## Purpose

- Responsible for the core LMS REST API: users/roles, courses, materials, assignments, attempts/results, help requests, imports, auth/session support, and API contracts for downstream clients.
- Not responsible for Telegram bot UX, SPW frontend rendering, content strategy, or content pipeline orchestration; those live in TG_LMS, SPW, ContentFactory, and ContentBackbone.

## AI-Facing Profile

- Stack: Python 3.10+, FastAPI, Pydantic v2, SQLAlchemy 2.x async, Alembic, PostgreSQL, Redis where enabled, pytest.
- Main entry: `python run.py` starts Uvicorn on `http://localhost:8000`.
- API docs: `/docs`, `/redoc`, and tracked `docs/openapi.json` when present.
- Auth: API key via `api_key` query parameter from `VALID_API_KEYS`; never hardcode or document real keys.
- Database changes: Alembic only. Trigger logic belongs in migrations, not service code.

## Durable Context

- LMS is a shared upstream contract for TG_LMS and SPW. Endpoint names, response schemas, auth behavior, and OpenAPI drift must be treated as cross-project risks.
- Google Sheets import and LMS content imports may depend on secrets under `.env` or `secrets/`; do not expose values.
- Historical issue pattern: implementation changed endpoint names/schemas without updating specs/ADR, causing downstream 404/schema failures.

## Commands

- Setup: `python -m venv .venv`; `.venv\Scripts\activate`; `pip install -r requirements.txt`.
- DB migrate: `alembic upgrade head`.
- Run locally: `python run.py`.
- Test: `pytest tests/`.
- Focused smoke: `curl http://localhost:8000/health`; `curl "http://localhost:8000/api/v1/users/?api_key=<dev-key>"`.
- Migration create: `alembic revision --autogenerate -m "<message>"`.

## Required Skills

- Use `fastapi-api-developer` for API feature/debug work.
- Use `db-check` for schema, migration, invariant, or data-sensitive changes.
- Use `context-auditor` when code, OpenAPI, specs, ADR, TG_LMS, or SPW disagree.
- Use `qa-report`/`qa-fix` for endpoint smoke, regression, and QA-driven remediation.
- Use `techlead-code-reviewer` or `lms-fastapi-techlead-code-reviewer` before risky backend integration.
- Use `release-prep` before merge/deploy with migrations, auth/session changes, external writes, or downstream contract impact.

## Architecture Notes

- Core modules: `app/api/v1`, `app/services`, `app/repos`, `app/models`, `app/schemas`, `app/core`, `app/auth`, `app/utils`.
- Data/storage: PostgreSQL via SQLAlchemy; migrations under `app/db/migrations` and `alembic.ini`.
- External services: Google APIs for imports; email/Resend-style services where configured; Redis where enabled.
- Trust boundaries: API keys, session/auth, imported content, file uploads, external service callbacks, and downstream client contracts.

## Known Risks

- Reliability: migrations, triggers, and async DB behavior can break primary API paths.
- Security/privacy: API keys, user identity, session/auth, uploads, Google credentials, and email links are sensitive.
- Data/encoding: Cyrillic docs and imported content require explicit UTF-8 handling.
- Cross-project contract drift: TG_LMS and SPW can fail if OpenAPI/specs are not synced with implemented routes and schemas.

## Smoke Checks

- Health endpoint responds.
- Alembic is at head before DB-dependent work.
- OpenAPI route names match any changed docs/specs.
- For integration work, curl every endpoint named in the task/spec before handoff.
- For auth/session work, verify both success and failure paths without exposing secrets.

## Current Decisions

| Date | Decision | Why | Owner/Source |
| --- | --- | --- | --- |
| 2026-05-26 | Treat OpenAPI/code/spec sync as mandatory for integration changes. | Prevent TG_LMS/SPW contract drift. | IDE_booster error register |

## Prevention Register

| Date | Incident/Risk | Prevention Rule | Related Skill |
| --- | --- | --- | --- |
| 2026-04-28 | Downstream clients hit stale endpoint names/schemas. | If implementation diverges from spec/ADR/OpenAPI, update the contract artifact or record explicit deviation in the same task. | `context-auditor`, `fastapi-api-developer`, `qa-fix` |
| 2026-05-26 | Secrets in local config/docs risk leakage. | Never copy `.env`, API keys, Google credentials, tokens, or session data into docs or final answers. | `encoding-guard`, `project-docs` |

## Handoff Notes

- Current focus: keep LMS as stable upstream API for bot/frontend/content integrations.
- Blockers: live DB, valid dev API key, and external credentials may be needed for full smoke; use placeholders in docs.
- Follow-ups: keep `docs/openapi.json` and integration specs current after endpoint/schema changes.

## Maintenance Rules

- Keep durable facts here; keep transient task notes in session summaries or issue docs.
- Do not store credentials, tokens, cookies, personal secrets, or private keys.
- When implementation intentionally diverges from specs, record the decision and update the relevant specs/docs in the same task.
- Prefer links to canonical docs over duplicating long content.
