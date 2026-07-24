@AGENTS.md

# CLAUDE.md — LMS Core API

## Project Overview

LMS — backend FastAPI + PostgreSQL (`learn` DB). Курсы, материалы, задания, попытки, результаты, ручная проверка преподавателями.
Источник правды по стратегии — ContentBackbone (`D:\Work\ContentBackbone\`). Frontend клиент — SPW (`D:\Work\spw\`). TG-боты — TG_LMS (`D:\Work\TG_LMS\`).

**Текущий статус:** именованные фазы (Y-1…Y-6, passwordless auth + учебный движок) завершены; разработка идёт точечными задачами `tsk-NNN` (кросс-проектный трекер `D:\Work\Root\tasks/`) — последние: forced attempt-limit на сервере при приёме ответа (tsk-269), попытки по паре «курс + задание» (tsk-264), normalization-driven импорт заданий из Google Sheets (tsk-267).

## Tech stack

Python 3.10+ · FastAPI · SQLAlchemy 2.x async · Alembic · PG 13+ · Redis 7
Auth: passwordless (email magic-link + TG initData + VK ID 2.0 PKCE) + service-level X-API-Key
Migrations: `app/db/migrations/versions/` (Alembic, schema=public)

## Authority documents (источники правды для LMS-разработки)

- `D:\Work\LMS\docs\specs\2026-04-27-tech-spec-Y1-auth-extension.md` — авторитетный контракт Y-1 auth
- `D:\Work\LMS\docs\openapi.json` — машино-читаемый API контракт (генерируется FastAPI)
- `D:\Work\LMS\docs\frontend-contract-sa-com.md` — формат ответов SA_COM
- `D:\Work\LMS\docs\learning-engine-next-item.md` — техдок учебного движка
- `D:\Work\LMS\docs\ai\adr\0001-auth-passwordless-multi-identity.md` — LMS-side ADR
- **`D:\Work\ContentBackbone\docs\ai\ege-import-playbook.md`** — плейбук импорта заданий ЕГЭ/ОГЭ/Python (карта источников, дефекты F1–F10, durable-инварианты). Читать до любой работы с заданиями ЕГЭ/ОГЭ, внешними источниками, `solution_rules`/`difficulty`/`order_position`/стемами/медиа. LMS-стаб: `docs/qa/2026-07-24-ege-import-playbook-pointer.md` (tsk-399)

## Cross-project memory (ОБЯЗАТЕЛЬНО)

Этот проект — часть семьи из 4 связанных проектов. Источник cross-project памяти (контракты, changelog, состояние):

**`D:\Work\ContentBackbone\docs\cross-project\`**

### Перед задачей

Если задача может затронуть SPW / TG_LMS / ContentBackbone (новый/изменённый HTTP endpoint, миграция Alembic, изменение public schema, bump зависимости) — прочитать в Шаге 0:

1. `D:\Work\ContentBackbone\docs\cross-project\STATE.md` — текущая фаза всех проектов
2. `D:\Work\ContentBackbone\docs\cross-project\CHANGELOG.md` — последние 14 дней
3. `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-api.md` — авторитетный mirror LMS API
4. `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-db-schema.md` — Alembic head + таблицы

### После cross-project изменения

Обязательно обновить (Edit) **3 файла** в `D:\Work\ContentBackbone\docs\cross-project\`:
1. `contracts/lms-api.md` (если изменился endpoint) **или** `contracts/lms-db-schema.md` (если миграция)
2. `CHANGELOG.md` (append в начало) — новая запись с Project/Change/Impact/Action/Authority/Refs
3. `STATE.md` (если фаза/версия сменилась)

Затем — `git add docs/cross-project && git commit -m "cross-project: LMS <change>"` в **ContentBackbone**.

### Полный стандарт

`~/.claude/skills/claude-booster/references/cross-project-memory-standard.md`

### Триггеры cross-project задачи

- HTTP endpoint (новый / изменение / удаление / переименование)
- Alembic миграция в `learn` DB
- Изменение Pydantic-схемы публичного API
- Bump FastAPI / SQLAlchemy / Pydantic / Python
- Изменение CORS allow-list, rate-limits
- Новый ADR с cross-project impact

## Coding conventions

- Type hints обязательны
- Docstrings RU
- `logging` вместо print
- UTF-8 без BOM
- Коммиты RU, императив: `<тип>: <описание>` (feat/fix/refactor/docs/style/test/chore/perf/ci)

## Глобальные правила

- `~/.claude/CLAUDE.md` — глобальный контекст IDE_booster
