# Learning Engine V1: расчёт next item и производительность

Краткий техдок по сервисному слою этапа 2 (без REST).

## Состояние курса (dependency-gate)

`compute_course_state(student_id, course_id)` считает прогресс по **дереву курса** (course_id + все потомки через `course_parents`): `total_tasks` и `tasks_with_result` — по всем заданиям в дереве. Поэтому `COMPLETED` означает завершение всего курса (включая подкурсы); блокировка `blocked_dependency` в `resolve_next_item` корректна.

## Где и как считается next item

- **Метод:** `LearningEngineService.resolve_next_item(db, student_id)` → `NextItemResult`.
- **Порядок:**
  1. Активные root-курсы из `user_courses` (`is_active=true`) по `order_number ASC NULLS LAST`, затем `course_id`.
  2. Для каждого root проверка зависимостей: все `course_dependencies.required_course_id` должны иметь состояние `COMPLETED` (таблица `student_course_state`; при необходимости вызывается `compute_course_state` с upsert).
  3. Обход дерева курса: root и потомки по `course_parents.order_number` (рекурсия через `get_child_rows`; результат кешируется на время сессии БД, tsk-662).
  4. В каждом курсе: сначала первый незавершённый материал (`student_material_progress.status != 'completed'`, порядок по `materials.order_position`), затем первое задание не в состоянии PASSED и не BLOCKED_LIMIT (состояния всех заданий узла считаются ПАКЕТОМ через `compute_task_states_batch` с границей корня, tsk-662).
- **Типы результата:** `material` | `task` | `none` | `blocked_dependency` | `blocked_limit`.

## Критичные по производительности запросы

- **resolve_next_item:** несколько запросов на один вызов: список активных `user_courses`, для каждого root — зависимости, для каждого курса в дереве — материалы и задания, на каждый курс дерева — ТРИ запроса на состояния всех его заданий сразу (`compute_task_states_batch`).
  - tsk-662: до правки состояние считалось поэлементно (`compute_task_state`, ~6 запросов на задание). Движок идёт по учебному порядку до первого незавершённого элемента, то есть проверяет ВСЁ уже пройденное — цена росла по мере прохождения курса и была наибольшей у самых сильных учеников. Замер на боевой базе (tsk-655): `GET /me/last-position` ученика 4551 — 694 запроса и ~108 с; после правки 106 запросов и ~21 с. Остаток объёма — обход дерева (67 запросов, по запросу на узел); `WITH RECURSIVE` там ещё не сделан.
  - Проверять изменения этого пути замером, а не на глаз: `scripts/measure_summary_cost_tsk655.py --prod --last-position <ID> --top 12` (только чтение) — число запросов не зависит от сети.
- **compute_task_state:** два запроса на задание (COUNT завершённых попыток, последняя попытка по `task_id`/`user_id`). Фильтры по `user_id`, `task_id`, `finished_at IS NOT NULL` — без full-scan при наличии индексов.
- **compute_course_state:** подсчёт заданий курса, подсчёт заданий с результатом (join `task_results` + `attempts` + `tasks` по `course_id`/`user_id`). Индексы по `course_id`, `user_id`, `finished_at` критичны.
- **student_course_state:** upsert по `(student_id, course_id)` при `update_state_table=True` — два запроса: транзакционная блокировка ученика `pg_advisory_xact_lock` и сам upsert (tsk-626; без блокировки два параллельных писателя одного ученика захватывали строки в разном порядке и падали с `DeadlockDetectedError`). Единственная точка записи — `learning_engine_service.upsert_course_state`; писатели, обходящие НЕСКОЛЬКИХ учеников за транзакцию (бэкфилл зависимостей, фоновый тик), обязаны идти по возрастанию `student_id`. Коммит не выполняется внутри сервиса; транзакцию завершает вызывающий код.

**Примечание:** `GET /api/v1/learning/next-item` при вызове может выполнять запись в БД (обновление `student_course_state` при проверке зависимостей). Для частых вызовов это даёт write-амплификацию; при необходимости read-only поведения обновление состояния можно вынести в отдельный endpoint или кэш.

## Рекомендации

- Не делать full-scan по `attempts`/`task_results` без фильтра по студенту/курсу/заданию.
- Для тяжёлых веток курса — предзагрузка списков task_id/material_id и батчевый расчёт состояний заданий.
