---
slug: tsk-004-tasks-order-position-testplan
parent_brief: tsk-004-tasks-order-position
created: 2026-05-21
---

# Тест-план: order_position для tasks

Ветка: feature/tsk-004-tasks-order-position
Репозиторий: vkomlev/LMS

## Затронутые модули

- [app/db/migrations/versions/<TS>_tasks_order_position_triggers.py](../../app/db/migrations/versions) — новая миграция
- [app/models/tasks.py](../../app/models/tasks.py) — колонка order_position
- [app/schemas/tasks.py](../../app/schemas/tasks.py) — TaskCreate/TaskUpdate/TaskRead/TaskUpsertItem +order_position
- [app/services/tasks_service.py](../../app/services/tasks_service.py) — bulk_upsert + get_by_course
- [app/services/learning_engine_service.py](../../app/services/learning_engine_service.py) — order_by Tasks.order_position NULLS LAST, Tasks.id

## Ключевые взаимодействия

### Триггер `trg_set_task_order_position` (BEFORE INSERT/UPDATE)
- T1. INSERT task в курс с 0 задач, `order_position=NULL` → задача получает 1
- T2. INSERT task в курс с N задач, `order_position=NULL` → задача получает N+1
- T3. INSERT task с явным `order_position=K` (1≤K≤N) → существующие с pos≥K сдвигаются на +1
- T4. INSERT task с `order_position=N+5` (дырка) → запись с pos=N+5 (триггер дырки не закрывает, как у materials)
- T5. UPDATE order_position N→M (M>N): записи с pos∈(N,M] сдвигаются на −1
- T6. UPDATE order_position N→M (M<N): записи с pos∈[M,N) сдвигаются на +1
- T7. UPDATE order_position → NULL: запись уходит в MAX+1, остальные с pos>old сдвигаются на −1
- T8. UPDATE order_position на тот же → no-op
- T9. Изоляция по course_id: INSERT/UPDATE/DELETE в курсе A не задевает курс B (с тем же order_position)

### Триггер `trg_reorder_tasks_after_delete` (AFTER DELETE FOR EACH STATEMENT)
- T10. DELETE одной task → order_position остальных пересчитан 1..N
- T11. **DELETE WHERE course_id=X (несколько одной транзакцией)** → нет TriggeredDataChangeViolationError, остальные курсы не затронуты (regression-тест по уроку materials)
- T12. DELETE последней задачи курса → no shift, нет ошибок
- T13. DELETE задач из нескольких курсов одной транзакцией → пересчёт независим по course_id

### Backfill миграции
- T14. После migrate up: SELECT id, order_position, ROW_NUMBER() OVER(PARTITION BY course_id ORDER BY id) совпадает для всех существующих 567 задач
- T15. После migrate up → migrate down → migrate up: order_position идентичен (идемпотентность бекфилла)

### Bulk-upsert
- T16. Bulk-upsert: создаёт новую task с `order_position=K` → попадает на позицию K, остальные сдвигаются
- T17. Bulk-upsert: создаёт новую task без order_position → MAX+1
- T18. Bulk-upsert: обновляет существующую task с новым `order_position` → пересчёт работает
- T19. Bulk-upsert: обновляет существующую task без order_position в payload → позиция НЕ меняется

### API endpoints
- T20. GET /tasks/by-course/{id} возвращает в порядке order_position NULLS LAST, id
- T21. GET /tasks/{id} возвращает поле order_position
- T22. POST /tasks с order_position=K создаёт на позиции K
- T23. PATCH /tasks/{id} с order_position меняет порядок
- T24. /tasks/search продолжает сортировать по id (regression — не должно сломаться)

### Learning Engine regression
- T25. `_collect_courses_in_order` → tasks порядок совпадает с before-state ДО миграции (после бекфилла по id ASC). Snapshot-тест:
  ```
  before: SELECT id FROM tasks WHERE course_id=X ORDER BY id
  after:  SELECT id FROM tasks WHERE course_id=X ORDER BY order_position NULLS LAST, id
  assert: одинаковые последовательности
  ```
- T26. Existing `test_y62_syllabus_states.py` и другие LE-тесты проходят без правок ожидаемых результатов

## Граничные случаи

- E1. INSERT с order_position=0 — допустимо ли? Materials не валидирует. Решение: следовать materials (разрешить, триггер сдвинет остальных как и для 1).
- E2. UPDATE order_position на огромное значение (> MAX+1) — то же поведение, что у materials (дырка остаётся).
- E3. DELETE с CASCADE из courses → каскад через FK → trg_reorder_tasks_after_delete сработает для каждого затронутого course_id.
- E4. Concurrent INSERT двух задач в один курс с NULL → SERIALIZABLE/READ COMMITTED поведение? Materials мог иметь race. **Проверить:** или принять как known-edge в контракте.

## Критические пути

- CP1. Pipeline миграции: alembic upgrade head → 567 задач имеют order_position, триггеры активны, индекс idx_tasks_course_order существует.
- CP2. Pipeline студента: POST /sessions → next-item → возвращает первую task по order_position.
- CP3. Pipeline импорта из Google Sheets: bulk-upsert успешно создаёт N задач в детерминированном порядке.

## Smoke-команды
```powershell
# Применить миграцию
alembic upgrade head

# MCP-проверка
SELECT course_id, COUNT(*) FILTER (WHERE order_position IS NULL) AS null_pos,
       MIN(order_position), MAX(order_position), COUNT(*)
FROM tasks GROUP BY course_id ORDER BY course_id;
# ожидание: null_pos=0 для всех курсов, MAX = COUNT, MIN = 1

# Триггер DELETE
BEGIN;
DELETE FROM tasks WHERE course_id = (SELECT id FROM courses LIMIT 1);
-- ожидание: нет TriggeredDataChangeViolationError
ROLLBACK;
```
