# Claude Code — LMS Core API

## Стек
- Python 3.10+, FastAPI, SQLAlchemy 2.x (async), Alembic, PostgreSQL
- Архитектура: `api → services → repos → models`

## Контекст проекта
Backend LMS API. Миграции только через Alembic. БД локально: `Learn`.

AI-слой документации: [docs/ai/INDEX.md](../docs/ai/INDEX.md). Прежде чем исследовать код — проверить, есть ли ответ там.

---

## MCP PostgreSQL

- Алиас: `postgresql`
- По умолчанию **read-only** (`SELECT`, schema checks, diagnostics)
- Write-операция — только при явном требовании задачи
- Schema changes — только через Alembic миграции

---

## Профиль: API-разработчик

### Date/Time Safety (критично)
- Дата/время из `text(...)` / raw SQL — нормализовать через helper перед сравнением
- Explicit type-guards в service layer перед SLA/TTL сравнениями
- Не сравнивать raw `str` с `datetime`
- Timezone-safe: naive datetimes — reject или normalize
- Обязательные negative-tests: `str`, naive `datetime`, `None`

### Debug loop (при багфиксах и smoke-провалах)
1. Воспроизвести с точным endpoint + input data
2. Log triage:
   ```
   python skills/core/fastapi-api-developer/scripts/log_triage.py --log-file logs/app.log --tail 4000 --top 10
   ```
3. Коррелировать с MCP DB checks (4xx/5xx паттерны, SQL аномалии)
4. Добавить / подтвердить failing test до фикса
5. Применить root-cause fix (не symptom)
6. Пересмотреть smoke + смежные сценарии

### Smoke-тестирование
- CURL для endpoint calls
- 3 источника валидации: response body + `logs/app.log` + MCP DB state
- Цикл при провале: логи → фикс → повтор

### Stop-условия
- Root cause требует деструктивного DB действия
- Поведение воспроизводимо только в production
- Нужны внешние данные / зависимости недоступны локально

---

## Documentation paths (skill → куда сохранять)

Карта обязательна: любой скилл, создающий документарный артефакт, использует путь из этой таблицы. При конфликте с глобальной инструкцией — **приоритет у этой мапы**.

| Skill | Куда | Формат имени |
|---|---|---|
| `/project-docs` | AI-слой: [docs/ai/](../docs/ai/); human-слой: [docs/](../docs/); корень: `README.md`, `.claude/CLAUDE.md` | фиксированные имена по шаблону skill |
| `/fastapi-api-developer` | Review-артефакты: [reviews/](../reviews/) | `YYYY-MM-DD-краткое-описание.{md,diff}` |
| `/techlead-code-reviewer`, `/review-gate`, `/pr-review` | Отчёты ревью: [reviews/](../reviews/) | `YYYY-MM-DD-краткое-описание-review.md` |
| `/document-release` | CHANGELOG: `CHANGELOG.md` (корень); release-notes: [docs/releases/](../docs/releases/) | `vX.Y.Z.md` |
| `/session-digest` | [docs/sessions/](../docs/sessions/) | `YYYY-MM-DD-session-digest.md` |
| `/retro` | [docs/retro/](../docs/retro/) | `YYYY-MM-DD-retro.md` (еженедельно, дата понедельника) |
| `/qa-report`, `/qa-only` | [docs/qa/](../docs/qa/) | `YYYY-MM-DD-qa-report.md` |
| `/qa-fix` | Review-артефакт: [reviews/](../reviews/) | `YYYY-MM-DD-qa-fix-описание.{md,diff}` |
| `/spec-writer` | Скорректированные ТЗ при необходимости сохранения: [docs/specs/](../docs/specs/) | `YYYY-MM-DD-spec-описание.md` |
| `/change-plan-architect`, `/tech-spec-composer` | [docs/specs/](../docs/specs/) | `YYYY-MM-DD-plan-описание.md` |
| `/response-quality-coach` (skill-defect) | Глобальный реестр: `~/.claude/skills/claude-booster/references/skills-errors.md` | по шаблону реестра |
| `/response-quality-coach` (ответ-дефект) | `d:/Work/IDE_booster/Docs/ai/ANSWER_ERRORS.md` | по шаблону реестра |

### Архивация старых артефактов
- Исторические ТЗ, smoke-результаты, stage-снимки и старые чаты живут в [docs/archive/](../docs/archive/) по подкатегориям: `tz/`, `smoke/`, `stages/`, `chats/`, `legacy/`.
- Review-артефакты pre-April 2026 — в [reviews/archive/](../reviews/archive/).
- Новые skills не пишут в archive/; это read-only хранилище для контекста.

### При создании новой директории из таблицы
Если директория (`docs/releases/`, `docs/sessions/`, `docs/retro/`, `docs/qa/`, `docs/specs/`) отсутствует — скилл создаёт её и добавляет `!docs/<dir>/` + `!docs/<dir>/**` в `.gitignore`.

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
- Secrets только в `.env`
- `/review-gate` обязателен перед интеграцией в `main`
- При изменениях БД — сначала `/db-check`
- Глобальный контекст: `~/.claude/CLAUDE.md`
- AI-слой проекта: [docs/ai/INDEX.md](../docs/ai/INDEX.md)
