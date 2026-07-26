# Review-gate: tsk-430 Календарь LMS Фаза 3 (панель преподавателя, перенос, ad-hoc)

**Gate Mode:** paranoid (новые публичные API-пути, IDOR-чувствительно, финальная фаза разблокирует tsk-410)
**Execution Posture:** report-only

## Decision

**PASS**

## Current-State Assessment

Финальная фаза Календаря LMS (кроме опциональной Фазы 4). Ноль новых
таблиц — переиспользует `lesson_occurrence`/`attendance_event`/
`operating_hours` (Фазы 1-2). Новые/правленые файлы:

- `app/repos/lesson_calendar_repository.py` — `LessonOccurrenceRepository.
  has_overlap` (реальный диапазон времени) + `create`/`list_for_teacher`.
- `app/services/lesson_calendar_service.py` — `ensure_user_has_role`
  (публичный, был приватным — переиспользуется Фазой 3),
  `is_within_operating_hours` (graceful None при неполной конфигурации).
- `app/services/lesson_occurrence_service.py` (новый) — панель
  преподавателя (`list_for_teacher` с живым `is_overdue`,
  `record_teacher_attendance`), `create_ad_hoc_occurrence` (общий для
  teacher add-student и student ad-hoc), `reschedule_occurrence`,
  `list_available_slots`.
- `app/api/v1/teacher_lesson_occurrences.py` (новый) — 3 эндпоинта,
  гейт по паттерну `teacher_workload.py` (explicit `teacher_id` +
  `get_current_user` + ручная ownership-проверка).
- `app/api/v1/lesson_occurrences.py` — +3 student-эндпоинта
  (`available-slots`, `reschedule`, `ad-hoc`).
- `app/schemas/lesson_calendar.py` — +6 схем.
- `tests/test_lesson_teacher_panel_tsk430.py` — 15 тестов.

## Consumed Review Artifacts

Нет — первый проход этой фичи.

## Blocking Issues

Нет.

## Non-Blocking Improvements

1. `is_within_operating_hours`/`list_available_slots` не обрабатывают
   переход занятия через полночь операционного дня (см. комментарий в
   коде) — MVP-ограничение, документировано, не считается дефектом:
   ночных занятий в требованиях оператора нет.
2. `has_overlap` в `LessonOccurrenceRepository` использует грубую
   ±1-день границу перед точной проверкой пересечения — тот же паттерн
   простоты, что и `LessonSlotRepository.has_overlap` (риск-регистр плана
   уже фиксирует эскалацию до `EXCLUDE USING gist` при реальных дублях).
3. `record_teacher_attendance` переиспользует
   `STUDENT_LESSON_ATTENDANCE_RECORDED` audit-константу для teacher-actor
   (различается через `details.actor_role`) вместо отдельной константы —
   сознательно, чтобы не плодить почти-дублирующиеся event-типы.

## Docs/Config/Runtime Drift Assessment

- `docs/ai/data-model.md` — секция «Календарь LMS Фаза 1» дополнена блоком
  «Фаза 3 (применено)».
- `docs/ai/architecture.md` — запись tsk-430 в списке точечных задач,
  явно отмечено разблокирование tsk-410.
- `docs/openapi.json` — регенерируется pre-commit хуком автоматически.
- Новых env-переменных нет (Фаза 3 переиспользует
  `LESSON_NO_SHOW_THRESHOLD_MINUTES` из Фазы 2 для `is_overdue`).

## Public API Contract Assessment

6 новых эндпоинтов (3 teacher + 3 student) задокументированы в
cross-project mirror `contracts/lms-api.md` этим же изменением.

## Cross-Project Sync Assessment

Обновлено в ContentBackbone (отдельный коммит): `contracts/lms-api.md`
(новая секция), `CHANGELOG.md`. Схема БД не менялась. SPW/TG_LMS — UI
потребление отдельной задачей в SPW, вне этого коммита; **это финальная
LMS-фаза Календаря, разблокирующая tsk-410** — явно отмечено в CHANGELOG
для видимости следующему исполнителю.

## Repository Hygiene Assessment

`git status` — изменения изолированы к файлам фичи; чужой WIP параллельных
сессий не тронут, коммит идёт явным pathspec.

## Required Fixes

Нет.

## Required Tests

Выполнено: 15 новых тестов — teacher list 403 ownership, `is_overdue`
(только для `scheduled`+прошедшее время, не для `confirmed`/будущего),
manual_present/manual_absent (включая исправление ошибочного `no_show`),
409 на `rescheduled`, add-student (201, 403 teacher-mismatch, 422 роль,
409 коллизия), available-slots (пусто без `operating_hours`, кандидаты
строго в часах работы), reschedule (создание нового + `rescheduled` у
старого, 422 вне часов работы), ad-hoc (201, 409 коллизия). Полный прогон:
**1013 passed, 11 skipped** (было 998 до Фазы 3 — ровно +15), регрессий нет.

## Required Validation Commands

```bash
cd D:/Work/LMS
.venv/Scripts/python.exe -m pytest tests/test_lesson_teacher_panel_tsk430.py -q
.venv/Scripts/python.exe -m pytest -q
```

## Residual Risks

- `available-slots`/`reschedule`/`ad-hoc` без сконфигурированных
  `operating_hours` либо не блокируют (ad-hoc/reschedule — graceful None),
  либо возвращают пустой список (available-slots) — осознанный MVP-выбор,
  не должен удивить оператора при первом использовании без настройки часов
  работы школы.
- Коллизии — сервисная проверка, не DB constraint (тот же риск, что и
  Фаза 1 `lesson_slot`).

## Next Safe Step

Коммит + пуш в LMS `main` (review-gate PASS → коммит). Затем — отдельный
коммит cross-project docs в ContentBackbone. Затем — обновить
`tsk-430`→`done`, декомпозицию `tsk-021`, и явно отметить в `tsk-410`, что
блокер снят (её саму НЕ брать в работу без отдельного решения оператора —
она не входит в объём этой сессии).
