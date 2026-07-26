# Review-gate: tsk-429 Календарь LMS Фаза 2 (явка ученика + reminder/no-show)

**Gate Mode:** paranoid (новый пользовательский API-путь + cron, IDOR-чувствительно)
**Execution Posture:** report-only

## Decision

**PASS**

## Current-State Assessment

Первая фаза Календаря LMS с реальным пользовательским эффектом. Никаких
новых таблиц — переиспользует `lesson_occurrence`/`attendance_event`
(Фаза 1) и существующий `Notifications` inbox. Новые файлы/правки:

- `app/services/lesson_attendance_service.py` — ownership + status-lock +
  запись `attendance_event` + audit.
- `app/services/lesson_attendance_cron_service.py` — reminder (once-only) +
  auto-no_show (только `status='scheduled'`), advisory-lock ключ `0x4C534E41`
  (уникален относительно Y-6 `0x59365453` и генератора Фазы 1 `0x4C534E43`).
- `app/api/v1/lesson_occurrences.py` — `POST .../attendance`,
  `GET /me/lesson-occurrences`, гейт `require_authenticated`.
- `app/repos/lesson_calendar_repository.py` (+`LessonOccurrenceRepository`),
  `app/schemas/lesson_calendar.py` (+3 схемы), `app/services/audit_service.py`
  (+1 константа), `app/core/config.py` (+3 env-настройки, безопасные дефолты).
- `app/api/main.py` — роутер + APScheduler startup/shutdown hooks.
- `tests/test_lesson_attendance_tsk429.py` — 9 тестов.

## Consumed Review Artifacts

Нет — первый проход. Self-review этой же сессией (см. Blocking Issues ниже —
одна находка была поймана и исправлена ДО этого отчёта, не после).

## Blocking Issues

Нет на момент отчёта. **Поймано и исправлено в процессе, не блокер сейчас:**
первая версия no-show ветки включала `status IN ('scheduled', 'confirmed')`
в кандидаты — это переписало бы уже подтверждённую явку (`confirmed` = ученик
нажал «Я на занятии») обратно в `no_show` только из-за истёкшего времени.
Исправлено до коммита на `status = 'scheduled'` (только неотвеченные); тест
`test_no_show_does_not_touch_confirmed_occurrence` фиксирует инвариант.

## Non-Blocking Improvements

1. `record_attendance` не блокирует повторную смену `declined`→`joined`
   (только `no_show`/`completed`/`rescheduled` заблокированы) — осознанно:
   MVP допускает передумать до начала занятия; более строгий гейт можно
   добавить в Фазе 3, если понадобится.
2. Reminder-идемпотентность через `NOT EXISTS` по `notifications.payload`
   вместо отдельного маркера в `lesson_occurrence` — минимизирует схему,
   но делает лог уведомлений частью инварианта. Приемлемо: `Notifications`
   уже используется как источник истины для read-статусов в других частях
   системы (inbox).

## Docs/Config/Runtime Drift Assessment

- `docs/ai/data-model.md` — секция «Календарь LMS Фаза 1» дополнена блоком
  «Фаза 2 (применено)». В синхроне.
- `docs/ai/architecture.md` — новая запись tsk-429 в списке точечных задач.
- `docs/openapi.json` — регенерируется pre-commit хуком автоматически.
- 3 новые env-переменные — безопасные дефолты в коде, `.env` не обязателен.

## Public API Contract Assessment

Новые эндпоинты `POST /lesson-occurrences/{id}/attendance`,
`GET /me/lesson-occurrences` — задокументированы в cross-project mirror
`contracts/lms-api.md` этим же изменением.

## Cross-Project Sync Assessment

Обновлено в ContentBackbone (отдельный коммит): `contracts/lms-api.md`
(новая секция), `CHANGELOG.md` (запись 2026-07-26). Схема БД не менялась —
`lms-db-schema.md` не трогается. SPW/TG_LMS — потребление начнётся отдельной
задачей в SPW (`/eng-review`, вне LMS-репозитория); explicit not-applicable
для этого коммита.

## Repository Hygiene Assessment

`git status` — изменения изолированы к файлам фичи; чужой WIP параллельных
сессий (`skills/core/**`, `reviews/2026-07-24..25-*`, `scratchpad/` и т.п.)
не тронут, коммит идёт явным pathspec.

## Required Fixes

Нет.

## Required Tests

Выполнено: 9 новых тестов — joined/declined 200, 403 IDOR (чужой ученик),
404 (несуществующий occurrence), 409 (уже `no_show`), список scoped +
`from`/`to` фильтр, reminder once-only (2 тика подряд — 1 уведомление),
no-show помечает только `scheduled` (создаёт `attendance_event` +
2 уведомления), no-show НЕ трогает `confirmed`. Полный прогон:
**998 passed, 11 skipped** (было 989 до Фазы 2 — ровно +9), регрессий нет.

## Required Validation Commands

```bash
cd D:/Work/LMS
.venv/Scripts/python.exe -m pytest tests/test_lesson_attendance_tsk429.py -q
.venv/Scripts/python.exe -m pytest -q
```

## Residual Risks

- Reminder/no-show cron работает на `scheduled_at` в UTC, конвертированном
  из локального времени слота при генерации (Фаза 1) — таймзона уже
  зафиксирована на этапе генерации occurrence, эта фаза её не пересчитывает
  повторно, риска рассинхронизации нет.
- SPW-баннер/UI подтверждения — не реализован (отдельная задача), без него
  API технически доступен, но пользователь не увидит кнопку в интерфейсе.

## Next Safe Step

Коммит + пуш в LMS `main` (review-gate PASS → коммит, стоячая авторизация
оператора). Затем — отдельный коммит cross-project docs в ContentBackbone.
Затем — обновить `tsk-429`→`done`, декомпозицию `tsk-021`, продолжить без
подтверждения к Фазе 3 (`tsk-430`) по указанию оператора.
