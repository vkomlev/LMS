# Review: Семантические тесты этапа 6 + правка get_stats_by_task

**Дата:** 2026-02-25

**Контекст:** По замечанию добавлены тесты семантики last-attempt (last хуже best / last лучше best) и исправлен ранний возврат в `get_stats_by_task` при `total_attempts == 0`.

**Изменения:**

1. **tests/test_last_attempt_stage6.py**
   - Подмена `_last_attempts_flat` через `unittest.mock.patch.object(..., AsyncMock)` и проверка точных значений агрегатов:
     - `test_last_worse_than_best_aggregates`: две задачи (last 2/10 FAIL, 10/10 PASS) → passed=1, failed=1, progress_percent=50, last_score=12, last_ratio=0.6.
     - `test_last_pass_main_status`: одна задача last 5/10 → PASS, passed_tasks_count=1, progress_percent=100.
     - `test_last_fail_main_status`: одна задача last 4/10 → FAIL, passed_tasks_count=0, progress_percent=0 (регресс-тест).
     - `test_no_completed_attempts`: last_rows=[] → passed=0, failed=0, progress_percent=0, current_score=0.
     - `test_by_task_last_passed_failed_counts`: по одной задаче два пользователя (last 10/10 и 3/10) → last_passed_count=1, last_failed_count=1, progress_percent=50.

2. **app/services/task_results_service.py**
   - В `get_stats_by_task` при `total_attempts == 0` в ответе теперь подставляются уже посчитанные по `last_rows` значения: `progress_percent`, `passed_tasks_count`, `failed_tasks_count`, `last_passed_count`, `last_failed_count` (раньше возвращались нули и ломали тест с подменёнными last-данными).

Полный diff: [2026-02-25-stage6-semantic-tests.diff](2026-02-25-stage6-semantic-tests.diff)
