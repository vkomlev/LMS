# Learning Engine V1: расчёт next item и производительность

Краткий техдок по сервисному слою этапа 2 (без REST).

## Состояние курса (dependency-gate)

`compute_course_state(student_id, course_id)` считает прогресс по **дереву курса** (course_id + все потомки через `course_parents`): `total_tasks` и `tasks_with_result` — по всем заданиям в дереве. Поэтому `COMPLETED` означает завершение всего курса (включая подкурсы); блокировка `blocked_dependency` в `resolve_next_item` корректна.

## Где и как считается next item

- **Метод:** `LearningEngineService.resolve_next_item(db, student_id)` → `NextItemResult`.
- **Порядок:**
  1. Активные root-курсы из `user_courses` (`is_active=true`) по `order_number ASC NULLS LAST`, затем `course_id`.
  2. Для каждого root проверка зависимостей: все `course_dependencies.required_course_id` должны иметь состояние `COMPLETED` (таблица `student_course_state`; при необходимости вызывается `compute_course_state` с upsert).
  3. Обход дерева курса: root и потомки по `course_parents.order_number` (рекурсия через `get_children`).
  4. В каждом курсе: сначала первый незавершённый материал (`student_material_progress.status != 'completed'`, порядок по `materials.order_position`), затем первое задание не в состоянии PASSED и не BLOCKED_LIMIT (состояние через `compute_task_state` по последней завершённой попытке).
- **Типы результата:** `material` | `task` | `none` | `blocked_dependency` | `blocked_limit`.

## Критичные по производительности запросы

- **resolve_next_item:** несколько запросов на один вызов: список активных `user_courses`, для каждого root — зависимости, для каждого курса в дереве — материалы и задания, для каждого задания при обходе — `compute_task_state` (подсчёт попыток + последняя попытка). При большом числе заданий в курсе возможен N+1 по task state; для горячих путей стоит рассмотреть батчинг состояний заданий по курсу.
- **compute_task_state:** два запроса на задание (COUNT завершённых попыток, последняя попытка по `task_id`/`user_id`). Фильтры по `user_id`, `task_id`, `finished_at IS NOT NULL` — без full-scan при наличии индексов.
- **compute_course_state:** подсчёт заданий курса, подсчёт заданий с результатом (join `task_results` + `attempts` + `tasks` по `course_id`/`user_id`). Индексы по `course_id`, `user_id`, `finished_at` критичны.
- **student_course_state:** upsert по `(student_id, course_id)` — один запрос при `update_state_table=True`. Коммит не выполняется внутри сервиса; транзакцию завершает вызывающий код.

**Примечание:** `GET /api/v1/learning/next-item` при вызове может выполнять запись в БД (обновление `student_course_state` при проверке зависимостей). Для частых вызовов это даёт write-амплификацию; при необходимости read-only поведения обновление состояния можно вынести в отдельный endpoint или кэш.

## Рекомендации

- Не делать full-scan по `attempts`/`task_results` без фильтра по студенту/курсу/заданию.
- Для тяжёлых веток курса — предзагрузка списков task_id/material_id и батчевый расчёт состояний заданий.
