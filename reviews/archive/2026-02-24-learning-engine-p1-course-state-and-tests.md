# Review: Learning Engine P1 — course_state по дереву и строгие тесты

**Дата:** 2026-02-24

**Контекст:** Правки по ревью: (1) P1 — `compute_course_state` считает прогресс по дереву курса (root + descendants), чтобы dependency-gate давал корректный COMPLETED; (2) P1 — тесты с детерминированными сценариями и строгими assert (default 3, OPEN/IN_PROGRESS, PASSED, FAILED).

**Изменения:**
- `app/services/learning_engine_service.py`: расчёт `total_tasks` и `tasks_with_result` по списку `tree_ids` из `_collect_courses_in_order`; запрос с `t.course_id = ANY(:course_ids)`.
- `tests/test_learning_engine_service.py`: строгие проверки (assert limit==3, state in OPEN/IN_PROGRESS с проверкой attempts_used, state==PASSED с проверкой ratio, новый тест FAILED с одной попыткой <0.5), новый тест `test_compute_task_state_failed`.
- `docs/learning-engine-next-item.md`: добавлен блок про состояние курса по дереву.

Полный diff: [2026-02-24-learning-engine-p1-course-state-and-tests.diff](./2026-02-24-learning-engine-p1-course-state-and-tests.diff)
