# Review-gate: tsk-428 Календарь LMS Фаза 1 (модель данных + admin-расписание)

**Gate Mode:** paranoid (миграция схемы + новый admin API)
**Execution Posture:** report-only

## Decision

**PASS**

## Current-State Assessment

Чистый лист: до этой миграции в LMS не было модели расписания/групп/
посещаемости. Изменения аддитивны — 4 новые таблицы
(`operating_hours`, `lesson_slot`, `lesson_occurrence`, `attendance_event`),
ноль изменений в существующих моделях/эндпоинтах. Applied в dev (`Learn`),
независимо верифицировано read-only через MCP `learn_public_db`.

Изменённые/новые файлы (LMS):
- `app/db/migrations/versions/20260726_010000_tsk428_lesson_calendar_stage1.py`
- `app/models/{operating_hours,lesson_slot,lesson_occurrence,attendance_event}.py`
- `app/db/base.py` (регистрация моделей)
- `app/repos/lesson_calendar_repository.py`
- `app/services/{lesson_calendar_service,lesson_occurrence_generator_service}.py`
- `app/schemas/lesson_calendar.py`
- `app/api/v1/lesson_calendar_admin.py`
- `app/api/main.py` (роутер + APScheduler startup/shutdown hooks)
- `app/core/config.py` (2 новых env-настройки, дефолты безопасны)
- `tests/test_lesson_calendar_tsk428.py` (13 тестов)
- `docs/ai/{data-model,architecture}.md` (docs sync)

## Consumed Review Artifacts

Нет предшествующего techlead-code-reviewer/qa-report артефакта — это
первый проход по этой фиче; независимый self-review ниже.

## Blocking Issues

Нет.

## Non-Blocking Improvements

1. `_lesson_slot_repo.has_overlap` грузит все активные слоты преподавателя/
   ученика на день недели в Python и сравнивает интервалы вручную вместо
   SQL-диапазонного оператора/`EXCLUDE USING gist` — осознанное решение по
   простоте (см. спек § Simplification Decisions), нагрузка тривиальна для
   MVP-объёма (единичные операторские вставки). Эскалировать до constraint
   при первом реальном инциденте дублей — уже зафиксировано в Risk Register
   плана.
2. `on_event("startup"/"shutdown")` — тот же паттерн, что уже используется
   Y-6 escalation scheduler (deprecated в FastAPI, но не новый долг —
   согласованность с существующим кодом важнее точечной модернизации).
3. `docs/ai/architecture.md` §«Регистрация роутеров» — список extra-роутеров
   уже был неполным до этой фичи (не включает teacher_assignments,
   teacher_progress и др.); новый `lesson_calendar_admin` туда тоже не
   добавлен — pre-existing staleness, не введена этим изменением, чинить
   отдельной задачей документационной уборки.

## Docs/Config/Runtime Drift Assessment

- `docs/ai/data-model.md` — новая секция «Календарь LMS Фаза 1» + строка в
  таблице миграций. В синхроне.
- `docs/ai/architecture.md` — новая запись в списке точечных tsk-задач. В
  синхроне.
- `docs/openapi.json` — регенерируется автоматически pre-commit хуком LMS
  при коммите (наблюдалось на предыдущих коммитах этой сессии) — ручного
  шага не требуется.
- `.env` — 2 новые переменные (`LESSON_OCCURRENCE_HORIZON_DAYS`,
  `LESSON_OCCURRENCE_CRON_INTERVAL_MIN`) имеют безопасные дефолты в коде
  (14 дней, 60 мин) — отсутствие переменных в `.env` не ломает запуск.

## Public API Contract Assessment

Новые эндпоинты (`PUT/GET /operating-hours`, `POST/GET/PATCH/DELETE
/lesson-slots{,/id}`) — admin-only, задокументированы в cross-project
mirror `contracts/lms-api.md` (ContentBackbone) тем же изменением. OpenAPI
регенерируется хуком при коммите.

## Cross-Project Sync Assessment

Обновлено в ContentBackbone (отдельный коммит по правилу проекта):
- `docs/cross-project/contracts/lms-api.md` — новая секция эндпоинтов.
- `docs/cross-project/contracts/lms-db-schema.md` — новая запись Alembic head.
- `docs/cross-project/CHANGELOG.md` — запись 2026-07-26.
SPW/TG_LMS не затронуты в Фазе 1 (admin-only, потребление начнётся в Фазе 2)
— explicit not-applicable, зафиксировано в CHANGELOG.

## Repository Hygiene Assessment

`git status` в LMS показывает мою фичу изолированно (5 modified + 10
untracked ровно по списку выше) плюс **чужой** WIP от параллельных сессий
(`skills/core/**`, `.claude/settings.local.json`, `reviews/2026-07-24..25-*`,
`reviews/tsk321-fill/`, `reviews/tsk391-sup/`, `reviews/tsk392-oge/*`,
`scratchpad/`) — не мои, коммит будет через явный pathspec, не
`git add -A`, чужое не трогается (см. `~/.claude/CLAUDE.md` §«Общий файл»/
chip_tree_gate). Коммит-сообщение — RU, императив, тип `feat`, по формату
проекта.

## Required Fixes

Нет.

## Required Tests

Выполнено: 13 новых тестов (`tests/test_lesson_calendar_tsk428.py`) —
weekday-конвенция, горизонт, пропуск уже прошедшего времени, идемпотентность
генератора (2 тика подряд), пропуск неактивного слота, admin 201/403/422/409,
soft-delete 204 + проверка `is_active=false` в БД, PUT operating-hours upsert
без дублей. Полный прогон `pytest -q`: **989 passed, 11 skipped**, регрессий нет.

## Required Validation Commands

```bash
cd D:/Work/LMS
.venv/Scripts/python.exe -m alembic heads
.venv/Scripts/python.exe -m pytest tests/test_lesson_calendar_tsk428.py -q
.venv/Scripts/python.exe -m pytest -q
```

## Residual Risks

- Коллизии слотов проверяются в приложении, не constraint'ом — риск гонки
  при одновременной операторской вставке (крайне маловероятно, единичный
  оператор). Зафиксировано в Risk Register плана.
- Прод-деплой этой миграции не выполнялся в рамках этой задачи (только dev)
  — намеренно: Фаза 1 — только фундамент, деплой будет вместе с первым
  пользовательским эффектом (Фаза 2) либо раньше по решению оператора.

## Next Safe Step

Коммит + пуш в LMS `main` (стоячая авторизация оператора на review-gate→
коммит, см. `~/.claude/CLAUDE.md` §«Operator handoff» ветка А). Затем —
отдельный коммит cross-project docs в ContentBackbone. Затем — обновить
статус `tsk-428`→`done` и декомпозицию `tsk-021` в Root-трекере, продолжить
без подтверждения к Фазе 2 (`tsk-429`) по указанию оператора.
