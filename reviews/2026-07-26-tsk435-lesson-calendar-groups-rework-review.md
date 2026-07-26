# Review-gate: tsk-435 Календарь LMS — rework на групповые слоты

**Gate Mode:** paranoid (breaking-миграция уже задеплоенных таблиц, переписан весь
слой сервисов/API/тестов Фаз 1-3)
**Execution Posture:** report-only

## Decision

**PASS**

## Current-State Assessment

Импорт реального расписания оператора (приватный Яндекс.Календарь) обнаружил, что
живая практика ГРУППОВАЯ (2-11 учеников на одно время с одним преподавателем) —
вразрез с исходным правилом «индивидуальное, не групповое» из Фазы 1 (tsk-428),
уже задеплоенной на прод. Оператор через `AskUserQuestion` явно выбрал полноценный
rework вместо обхода (N параллельных индивидуальных слотов).

**Breaking-миграция безопасна:** на момент rework все 4 таблицы Фазы 1
(`operating_hours`, `lesson_slot`, `lesson_occurrence`, `attendance_event`) были
пусты — 0 строк — и на dev, и на prod, проверено независимо через MCP до начала
работы. Ни одна реальная строка не потеряна.

**Изменения схемы** (`app/db/migrations/versions/20260726_020000_tsk435_lesson_calendar_groups.py`):
- `lesson_slot` — удалён `student_id` (+ FK + CHECK `student_teacher_distinct`).
- `lesson_occurrence` — удалены `student_id`, `status`, `rescheduled_to_id` (+ их FK/CHECK).
- Новая `lesson_slot_student` — M2M участники слота, `is_active` (мягкое удаление).
- Новая `lesson_occurrence_participant` — статус явки НА КАЖДОГО участника
  независимо, `rescheduled_to_occurrence_id`.
- `attendance_event` НЕ менялся — уже ключуется по (`occurrence_id`,
  `actor_user_id`), корректно работает для нескольких участников одного occurrence.
- Downgrade реализован и проверен (upgrade → downgrade → upgrade на dev, чисто).

**Переписан весь код фичи:**
- Модели: `lesson_slot.py`, `lesson_occurrence.py` (убраны поля) + 2 новых.
- Repos: `LessonSlotRepository.has_overlap` (teacher-only, было teacher+student),
  новые `LessonSlotStudentRepository`, `LessonOccurrenceParticipantRepository`
  (включая `has_student_overlap` — коллизии ТОЛЬКО по ученику, преподаватель по
  design может вести несколько occurrence одновременно).
- Сервисы: `lesson_calendar_service` (+`add_slot_participant` с бэкфиллом будущих
  occurrence, `+remove_slot_participant`), `lesson_occurrence_generator_service`
  (синк участников на каждый тик через `ON CONFLICT DO UPDATE` + `xmax=0` трюк
  для различения insert/update при `RETURNING`), `lesson_attendance_service`,
  `lesson_attendance_cron_service` (idempotency reminder — по `occurrence_id` И
  `user_id`, иначе групповое напоминание гасило бы соседей), `lesson_occurrence_service`
  (teacher-панель с массивом участников, `add_participant_to_occurrence`,
  reschedule на уровне участника — не трогает остальных).
- Схемы + 3 API-роутера — новые формы запросов/ответов (`student_ids[]` при
  создании слота, `TeacherAttendanceActionRequest.student_id`,
  `MyLessonOccurrenceRead`/`TeacherLessonOccurrenceRead` с `participants[]`).
- **Все 37 тестов Фаз 1-3 переписаны** под новую схему + добавлено 10 новых
  (группа не ломается при действии одного участника, reminder на каждого
  участника отдельно, teacher-панель с полным списком участников, add-participant
  к существующему occurrence, бэкфилл будущих occurrence при добавлении в слот).

## Consumed Review Artifacts

Нет предшествующего — это первый проход rework'а. Self-review этой же сессией.

## Blocking Issues

Нет.

## Non-Blocking Improvements

1. `xmax = 0` трюк для различения insert/update при `ON CONFLICT ... DO UPDATE
   ... RETURNING` — нестандартный для этой кодовой базы приём (Postgres-specific
   system column), но проверен вручную на реальном Postgres (temp table) перед
   использованием в генераторе — задокументирован inline-комментарием.
2. `reschedule_occurrence` всегда создаёт НОВЫЙ solo ad-hoc occurrence, не
   пытается «подсесть» в уже существующий групповой occurrence на новое время —
   осознанное упрощение MVP (задокументировано в докстринге), не считается
   дефектом: полноценное «влиться в существующую группу через reschedule» —
   отдельная возможная фича, не требование этой сессии.
3. `list_available_slots`/`is_within_operating_hours` не обрабатывают переход
   занятия через полночь операционного дня — то же MVP-ограничение, что было в
   Фазе 3, не тронуто rework'ом.

## Docs/Config/Runtime Drift Assessment

- `docs/ai/data-model.md` — секция «Календарь LMS Фаза 1» переименована в
  «Календарь LMS (tsk-428/429/430/435)», полностью описывает новую схему участников.
- `docs/ai/architecture.md` — новая запись tsk-435 с explicit backlink на находку
  и решение оператора.
- `docs/openapi.json` — регенерируется pre-commit хуком автоматически (формы
  запросов/ответов для lesson-slots/lesson-occurrences изменились).
- Новых env-переменных нет — rework переиспользует все settings Фаз 1-2.

## Public API Contract Assessment

Breaking-изменение публичного контракта на уже задеплоенных эндпоинтах
(`POST /lesson-slots` больше не принимает `student_id`, принимает `student_ids[]`;
ответы occurrence/participant меняют форму). Оправдано: прод-таблицы были пусты
(ни один реальный клиент/интеграция ещё не зависел от старой формы — SPW UI для
календаря ещё не построен). Cross-project mirror обновляется этим же изменением.

## Cross-Project Sync Assessment

Обновляется в ContentBackbone (отдельный коммит): `contracts/lms-db-schema.md`
(schema breaking change), `contracts/lms-api.md` (новая форма контрактов),
`CHANGELOG.md`. SPW/TG_LMS не затронуты (UI ещё не строился на старом контракте).

## Repository Hygiene Assessment

`git status` — изменения изолированы к файлам фичи (миграция, модели, repos,
сервисы, схемы, роутеры, тесты, docs); чужой WIP параллельных сессий не тронут,
коммит идёт явным pathspec.

## Required Fixes

Нет.

## Required Tests

Выполнено: 47 тестов в 3 файлах фичи (было 37, +10 новых на групповое
поведение), полный прогон **1023 passed, 11 skipped** (было 1013 до rework —
ровно +10), регрессий нет ни в одном из остальных 976 тестов проекта.

## Required Validation Commands

```bash
cd D:/Work/LMS
.venv/Scripts/python.exe -m alembic heads
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m pytest tests/test_lesson_calendar_tsk428.py tests/test_lesson_attendance_tsk429.py tests/test_lesson_teacher_panel_tsk430.py -q
.venv/Scripts/python.exe -m pytest -q
```

## Residual Risks

- Breaking-миграция необратима с сохранением данных (downgrade добавляет
  `student_id`/`status` обратно как nullable, без backfill реальных значений) —
  приемлемо, т.к. таблицы были пусты на момент миграции; станет неприемлемо,
  если на проде уже появятся реальные occurrence к моменту отката — учитывать
  при любом будущем откате.
- Импорт реального расписания (tsk-435, следующий шаг) создаст первые реальные
  строки — после этого откат миграции станет деструктивным. Прод-деплой этого
  rework должен пройти ДО импорта данных.

## Next Safe Step

Коммит + пуш в LMS `main` (review-gate PASS → коммит). Затем — отдельный коммит
cross-project docs в ContentBackbone. Затем — деплой на прод VPS (`lms-spw-vds`,
`deploy/vps/deploy.sh`) — таблицы пусты, миграция безопасна. Затем — живой смоук
через реальную сессию оператора. Только ПОСЛЕ успешного деплоя+смоука — импорт
реальных данных календаря (11 новых аккаунтов + 12 групповых слотов) по протоколу
`/db-check`.
