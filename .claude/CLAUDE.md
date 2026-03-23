# Claude Code — LMS API

## Стек
FastAPI, SQLAlchemy, Alembic, PostgreSQL

## Контекст проекта
Backend LMS API. Миграции только через Alembic. MCP Postgres read-only по умолчанию.

## Правила для этого проекта
- Не трогать `.cursorrules`, `AGENTS.md`, `skills/core/` — они для Cursor/Codex
- Secrets только в `.env`, не в коде
- Перед интеграцией в main/master — обязателен `/review-gate`
- При изменениях БД — сначала `/db-check`

## Tier H задачи для Claude
- Архитектурные решения и ревью
- Сложная отладка и расследования
- Финальный quality gate (`/review-gate`)
- UX/UI анализ через `/gstack` (browse)

## Глобальный контекст
См. `~/.claude/CLAUDE.md` — полный список skills и правила IDE_booster.
