# Claude Code — LMS API

## Стек
- Python, FastAPI, SQLAlchemy, Alembic, PostgreSQL
- Архитектура: `api → services → repos`

## Контекст проекта
Backend LMS API. Миграции только через Alembic. БД: `Learn` (локально).

---

## MCP PostgreSQL

- MCP алиас: `postgresql`
- По умолчанию: **read-only** (`SELECT`, schema checks, diagnostics)
- Write-операция — только при явном требовании задачи
- Schema changes — только через Alembic миграции

---

## Профиль: API разработчик

**Date/Time Safety (критично):**
- Дата/время из `text(...)` / raw SQL — нормализовать через helper перед сравнением
- Explicit type-guards в service layer перед SLA/TTL сравнениями
- Не сравнивать raw `str` с `datetime`
- Timezone-safe: naive datetimes — reject или normalize по правилу проекта
- Обязательные negative tests: `str` input, naive `datetime`, `None`

**Debug loop (при багфиксах и smoke-провалах):**
1. Воспроизвести с точным endpoint call и input data
2. Log triage:
   ```
   python skills/core/fastapi-api-developer/scripts/log_triage.py --log-file logs/app.log --tail 4000 --top 10
   ```
3. Коррелировать с MCP DB checks (4xx/5xx паттерны, SQL аномалии, transaction failures)
4. Добавить / подтвердить failing test до фикса
5. Применить root-cause fix (не symptom)
6. Пересмотреть smoke + смежные сценарии

**Smoke-тестирование:**
- CURL для endpoint calls
- 3 источника валидации: response body + `logs/app.log` + MCP DB state
- Цикл при провале: логи → фикс → повтор (прерывать только при необходимости действия от пользователя)

**Stop-условия:**
- Root cause требует деструктивного DB действия
- Поведение воспроизводимо только в production
- Нужны внешние данные / зависимости недоступны локально

---

## Review-changes — ОБЯЗАТЕЛЬНО

После каждого завершённого логического блока правок:
1. Markdown: `reviews/YYYY-MM-DD-краткое-описание.md` (заголовок, контекст, начало diff)
2. Diff: `reviews/YYYY-MM-DD-краткое-описание.diff`
   ```powershell
   git diff | Out-File -FilePath "reviews\имя.diff" -Encoding utf8
   ```

---

## Контракт вывода
- `Plan`
- `Changed Files`
- `Validation Commands`
- `DB Findings` (при работе с БД)
- `Log Findings` (при отладке)
- `Date/Type Guard Evidence` (при изменениях date/SLA/TTL логики)
- `Risks / Follow-ups`

---

## Общие правила
- Secrets только в `.env`, не в коде
- `/review-gate` обязателен перед интеграцией в main/master
- При изменениях БД — сначала `/db-check`
- Глобальный контекст: `~/.claude/CLAUDE.md`
