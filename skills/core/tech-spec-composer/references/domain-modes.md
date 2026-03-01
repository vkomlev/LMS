# Domain Modes

## telegram-bot
- Target stack: `aiogram` + `aiogram-dialog` (latest stable in project lockfile).
- Require explicit state/dialog map and callback data strategy.
- Minimize screen count and navigation depth.
- Validate role permissions and fallback messages for missing data.
- Include manual chat scenario checks plus automated tests where feasible.

## api-service
- Target stack: FastAPI + Pydantic + SQLAlchemy/Alembic (project standard).
- Require endpoint contracts (request/response/status codes/errors).
- Require migration and rollback path for schema changes.
- Include auth/permission and idempotency checks where applicable.
- Include integration/smoke commands.

## parser-pipeline
- Define source limits, retry policy, deduplication, and error handling.
- Require deterministic output schema and logging keys.
- Include dry-run mode and failure classification.
- Include rate-limit and anti-ban safeguards.

## publisher-integration
- Define target platform contract and publish state machine.
- Require safe retry semantics and duplicate prevention.
- Include content validation and sanitization policy.
- Include post-publish verification and reporting steps.

