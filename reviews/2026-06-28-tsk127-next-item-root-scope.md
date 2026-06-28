# tsk-127 — next-item scope по корню + root_course_id в ответах

**Дата:** 2026-06-28
**Задача:** tsk-127 (LMS-сторона handoff от SPW)
**Спецификация:** `D:\Work\spw\docs\handoff\lms-next-item-course-scope.md`
**Трекер:** `D:\Work\Root\tasks\tsk-127-spw-navigatsiya-po-kursu-i-pamyat-poslednego-kursa.md`

## Контекст / проблема

Две корневые причины SPW-багов навигации ученика:

1. `resolve_next_item` обходил **все** активные курсы ученика (`for uc in active`) и
   отдавал первый незавершённый элемент по `user_courses.order_number`. После сабмита
   в курсе B следующий шаг мог прийти из курса A → жалоба «следующий урок из другого курса».
2. `next-item` и `get_last_position` возвращали `course_id` **листового** подкурса (`cid`),
   а не корневого курса. SPW строит навигацию вокруг корней (`/me/courses` отдаёт только
   корни) → лист не находится → «Курс не найден».

## Что сделано

### 1. Необязательный фильтр по корню в next-item
- `GET /api/v1/learning/next-item` — добавлен query `root_course_id: int | None = None`.
- `LearningEngineService.resolve_next_item(db, student_id, root_course_id=None)`: если задан —
  `active` фильтруется по `uc.course_id == root_course_id` (обход только дерева этого корня);
  если `None` — прежнее поведение. **Обратная совместимость сохранена.**

### 2. root_course_id в ответах
- `NextItemResult` (dataclass) и `NextItemResponse` (Pydantic) — добавлен `root_course_id`.
  `resolve_next_item` прокидывает корень (`current_root_id = uc.course_id`) во все возвраты
  (material / task / blocked_limit / blocked_dependency).
- `LastPositionRead` — добавлены `root_course_id` + `root_course_uid`.
- `me_service.get_last_position` — для каждой ветки резолвит корень: для material/task берёт
  авторитетный `next_item.root_course_id` (uid догружает); для `course_completed` — через
  новый helper `_resolve_root_course` (рекурсия `WITH RECURSIVE` по `course_parents` среди
  активных курсов ученика, тот же граф, что в `_COURSES_PROGRESS_SQL`). Fallback: лист сам
  себе корень, если вне активных деревьев.

## Изменённые файлы

- `app/schemas/learning_engine.py` — `NextItemResult.root_course_id`
- `app/services/learning_engine_service.py` — `resolve_next_item`: параметр-фильтр + проброс корня
- `app/schemas/learning_api.py` — `NextItemResponse.root_course_id`
- `app/api/v1/learning.py` — query-параметр + проброс в сервис и в ответ
- `app/schemas/me.py` — `LastPositionRead.root_course_id` + `root_course_uid`
- `app/services/me_service.py` — helper `_resolve_root_course` + root-поля во всех ветках
- `docs/openapi.json` — регенерация (168 endpoints)

## Валидация

- `python scripts/export_openapi.py` → 168 endpoints; проверено: `next-item` имеет query
  `root_course_id`, `NextItemResponse`/`LastPositionRead` содержат root-поля.
- `pytest tests/test_learning_engine_service.py tests/test_learning_api_routes.py` → 10 passed.
- `pytest -k "me or last_position or root_course"` → 132 passed, 5 skipped.

## Риски / follow-ups

- **Backward compat:** все новые поля опциональны с дефолтами; новый query опционален.
  Существующие consumers (TG_LMS, старый SPW-клиент) не затронуты.
- **SPW (follow-up, другая сессия):** `pnpm gen:api-types`; заменить клиентский leaf→root
  self-heal и cross-course guard (`// tsk-127`) на прямое чтение `root_course_id`.
- Cross-project контракты обновлены в `D:\Work\ContentBackbone\docs\cross-project\`
  (lms-api.md, spw.md, CHANGELOG.md, STATE.md).
- `GET /next-item` по-прежнему выполняет write (upsert student_course_state) — поведение
  не изменилось.
