# review-gate: tsk-473 — ревизия сводки занятия (tsk-022/tsk-410)

## Контекст
Оператор после практической эксплуатации уже задеплоенной сводки
(`GET /teacher/lesson-occurrences/{id}/summary`) попросил:
1. Структура: клик по конкретному ученику → его личная сводка, не разворот
   карточек всех участников сразу.
2. Состав полей: раздел курса + позиция (отдельно от last_activity),
   раздельные `tasks_completed`/`theory_completed` (откат объединения от
   2026-07-27), ссылка на задание у «не решил с трёх раз» + помощь
   (открытая/закрытая).

Три открытых вопроса уточнены через `AskUserQuestion` ДО реализации (см.
`D:\Work\Root\tasks\tsk-473-...md`, «История движения»).

## Изменённые файлы
**LMS:**
- `app/schemas/lesson_calendar.py` — `TeacherSummaryHomework.completed` →
  `tasks_completed`+`theory_completed`; `TeacherSummaryCourseProgress` +
  `current_section_title`/`current_item_title`; `TeacherSummaryHelpRequest` +
  `task_id`/`resolution_comment`; `TeacherSummaryParticipant` +
  `closed_help_requests`.
- `app/services/teacher_lesson_summary_service.py` — раздельный подсчёт ДЗ;
  текущая позиция из уже читаемого `get_student_progress` (без новых SQL);
  `_load_help_requests` (открытые + закрытые в окне).
- `app/services/help_requests_service.py` — `list_help_requests` получил
  опциональный `student_id` (SQL-фильтр вместо постфильтра в Python).
- `tests/test_teacher_lesson_summary_tsk022_410.py` — 15 тестов (было 8),
  включая регресс на SQL-фильтр по ученику.

**SPW:**
- `components/teacher/TeacherLessonSummary.tsx` — список кликабельных строк
  вместо разворота карточек.
- `components/teacher/StudentSummarySheet.tsx` (новый) — личная сводка
  ученика в Sheet, тот же аффорданс, что `TaskHistorySheet` (tsk-349).
- `lib/teacher/use-teacher-lesson-summary.ts` — экспорт
  `TeacherSummaryHelpRequest`.
- `lib/api-types.ts` — перегенерирован из свежего `docs/openapi.json`.
- `tests/unit/teacher-lesson-summary-drilldown.test.tsx` (новый, 4 теста).

**Cross-project:** `D:\Work\ContentBackbone\docs\cross-project\` —
`contracts/lms-api.md` (новый раздел «Ревизия…tsk-473») + `CHANGELOG.md`
(запись 2026-07-30). `STATE.md` не менялся (фаза/версия не сменились).

## Находки и исправления в ходе ревью

**[LOGIC, исправлено до коммита]** `_load_help_requests` переключился с
`status_filter="open"` на `"all"`, но брал результат через постфильтр в
Python по общей (не per-student) странице `list_help_requests` с
`limit=200`. У сортировки по умолчанию (`priority ASC, due_at ASC NULLS
LAST, created_at ASC`) нет recency-приоритета — при большой истории
заявок учителя (сотни заявок по всем ученикам) недавняя закрытая заявка
ИМЕННО этого ученика могла не попасть в первые 200 строк и молча
потеряться. Фикс — `student_id` теперь SQL-фильтр
(`list_help_requests(student_id=...)`), `limit=200` — реальный потолок на
ОДНОГО ученика (там счёт на единицы, как и утверждает исходный
комментарий). Добавлен регресс-тест
`test_summary_help_requests_scoped_to_own_student_at_sql_level`.

## Проверка по измерениям (существенные)
1. **Соответствие целям** — все 3 уточнённых пункта + п.5/8 из декомпозиции
   реализованы. DRIFT нет.
2. **Корректность** — edge cases покрыты тестами: курс завершён (оба поля
   позиции `None`), позиция в корне курса (`current_section_title=None`),
   позиция в подкурсе, окно закрытых заявок (внутри/снаружи), cross-student
   утечка (regression).
3. **Миграции/БД** — Data Impact = read, DDL нет.
4. **Security/IDOR** — ownership-гейт (`get_occurrence_for_teacher`) и ACL
   курсов (`list_accessible_student_courses`) не менялись; новый SQL-фильтр
   `student_id` в `list_help_requests` — доп. сужение, не ослабление ACL
   (`HELP_REQUESTS_ACL_SQL` применяется как и раньше).
5. **Тесты** — LMS: 15/15 в файле сводки + полный набор 1162 passed/11
   skipped/0 failed. SPW: 4 новых unit + полный набор 684/684 passed.
   Live-browser проверка на проде — ОБЯЗАТЕЛЬНА до закрытия задачи (см.
   Operator handoff), ещё не выполнена на момент этого артефакта.
6. **Docs drift** — `docs/openapi.json` регенерирован, `lib/api-types.ts`
   регенерирован, cross-project контракт обновлён.
11. **Cross-project sync** — выполнено (contracts/CHANGELOG).
12. **Public API contract sync** — response schema изменился (breaking
    rename `homework.completed`); grep по TG_LMS/ContentBackbone на
    `homework.completed` — 0 совпадений (единственный потребитель — SPW,
    обновлён в этой же сессии).

## Решение
**ПРИНЯТО.**

## Operator handoff
Ветвь А (см. `operator-handoff-rules.md`): деплой LMS+SPW и живая
браузер-проверка на проде — выполняются агентом в этой же сессии, без
запроса подтверждения у оператора (коммит/пуш/деплой — стоячая
авторизация, live-check — обязательное условие ветви А для user-facing
изменения).
