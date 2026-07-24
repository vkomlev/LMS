# tsk-353 — Поиск задания в кабинете преподавателя по номеру/тексту

**Дата:** 2026-07-24 · **Профиль:** `/fastapi-api-developer` (LMS) + `/executor-pro` (SPW)
**Data Impact:** read (1 новый read-only эндпоинт, миграций нет)

## Контекст и цель

На живом уроке ученик называет номер или пересказывает содержание задания — преподавателю
нужно быстро найти его в кабинете, не листая дерево прогресса. Поиск ведёт в уже
существующую детальную карточку истории (tsk-349/406, `TaskHistorySheet`) — новая
карточка не строилась, задача только находит `task_id`.

## Реализация

### Backend (LMS)

- **Схема** `app/schemas/task_search.py` — `TaskSearchResult`/`TaskSearchResponse`.
- **Сервис** `app/services/task_search_service.py::search_tasks_for_teacher` — два режима:
  - число / `id-<N>` (видимый номер задания, tsk-309/311, регистронезависимо) — точный
    поиск по `tasks.id`;
  - иначе — полнотекстовый `ILIKE` по `task_content->>'stem'`/`'title'`, кандидатный пул
    200 до ACL-фильтра (без `pg_trgm` — по объёму задачи достаточно), `%`/`_`/`\` в
    запросе учителя экранированы (не работают как wildcard).
- **ACL** — ровно `manual_progress_service.can_edit_progress` (тот же гейт, что у tsk-297
  правки прогресса и tsk-349 истории задания), проверяется на `course_id` каждого
  кандидата (с кэшем на запрос, чтобы не дёргать повторно для заданий одного курса).
  Недоступное — просто отсутствует в выдаче (200 + пустой список), не 403 — не течёт
  различие «не найдено» vs «нет доступа» (тот же принцип, что у
  `list_accessible_student_courses`).
- **Задания с `course_id IS NULL` исключены всегда** (SQL-фильтр в текстовом режиме +
  явная проверка в режиме по номеру) — паритет с документированным в tsk-349 инвариантом
  «такое задание даёт 404 на эндпоинте истории». Защитный код: сейчас недостижимо —
  `tasks.course_id` — `NOT NULL`, подтверждено read-only через MCP на dev и prod (0 строк).
- **Роутер** `app/api/v1/teacher_task_search.py` — `GET /teacher/students/{student_id}/tasks/search`,
  гейт роли teacher/methodist/admin. Зарегистрирован в `app/api/main.py`.
- `openapi.json` регенерирован (179 эндпоинтов).

### Frontend (SPW)

- `lib/task-search/use-task-search.ts` — `useTaskSearch` (react-query) + `useDebouncedValue`
  (350мс, идиом `useAutoSaveDraft`).
- `components/task-search/TaskSearchBox.tsx` — инпут + состояния loading/error/empty/results.
- `components/teacher/StudentProgress.tsx` — `TaskSearchBox` подключён; клик по результату
  переиспользует уже существующий `historyTaskId` стейт → тот же `TaskHistorySheet`
  (tsk-349/406), новая карточка не строилась.
- `lib/api-types.ts` регенерирован из `openapi.json`.

## Гейты

- **LMS pytest:** 933 passed, 10 skipped (полная сюита). Новый `tests/test_task_search_tsk353.py`
  — 11 тестов: номер/`id-N`/регистронезависимость, ACL (teacher_courses-only ACL находит
  своё/не находит чужое, прямая связка ученик-учитель видит вне своего ACL — паритет с
  can_edit_progress, несвязанный учитель видит пусто), экранирование ILIKE (`%` как
  литерал, не wildcard — decoy-задание отличается ровно этим), интеграция с существующим
  эндпоинтом истории (найденный `task_id` открывается, не 404).
- **SPW:** vitest 513 passed (новые `task-search-box.test.tsx` — 5 тестов + `student-progress.test.tsx`
  +1 на интеграцию поиск→карточка), `tsc --noEmit` 0 ошибок, eslint 0 ошибок (1 не связанный
  warning в `scripts/live-auth.mjs`).
- **Cross-project mirror:** обновлены `contracts/lms-api.md`, `CHANGELOG.md`, `STATE.md` в
  ContentBackbone (`663684c`).
- **openapi.json** регенерирован в том же изменении; `lib/api-types.ts` (SPW) — тоже.

## Риски / follow-ups

- Кандидатный пул полнотекстового поиска (200) может не покрыть все совпадения на очень
  широком запросе — логируется (`logger.debug`), не блокирует: MVP-объём задачи не
  требует `pg_trgm`.
- Живой прод-прогон под ролью учителя — обязателен после деплоя (см. tsk-349, гейт живой
  проверки): найти задание по номеру, найти по тексту, открыть карточку — история и
  правило проверки видны учителю.
