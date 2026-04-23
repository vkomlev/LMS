# Review: Learning Engine V1, этап 6 — Last-attempt statistics

**Дата:** 2026-02-25

**Контекст:** Реализация этапа 6 по ТЗ `docs/tz-learning-engine-stage6-last-attempt-statistics.md`: переключение основных агрегатов прогресса и успеваемости на логику последней завершённой попытки (last-attempt), сохранение best/avg как дополнительных полей.

**Изменения:**

1. **LearningEngineService.compute_course_state**  
   Состояние курса COMPLETED считается только если по всем задачам дерева у студента последняя попытка — PASS (score/max_score >= 0.5). Раньше учитывалось наличие любой завершённой попытки по задаче. Используется CTE с DISTINCT ON (task_id) и фильтр по порогу.

2. **TaskResultsService**  
   - Константа `PASS_THRESHOLD_RATIO = 0.5`.  
   - Хелпер `_last_attempts_flat(db, *, user_id, task_id, task_ids)` — последняя попытка по (user_id, task_id) через ROW_NUMBER() OVER (PARTITION BY user_id, task_id ORDER BY finished_at DESC, id DESC).  
   - `_is_pass(score, max_score)` — признак PASS по порогу.  
   - **get_stats_by_user**: добавлены progress_percent, passed_tasks_count, failed_tasks_count, current_score, current_ratio, last_score, last_max_score, last_ratio; старые поля сохранены.  
   - **get_stats_by_course**: добавлены progress_percent, passed_tasks_count, failed_tasks_count; остальные поля по всем попыткам.  
   - **get_stats_by_task**: добавлены progress_percent, passed_tasks_count, failed_tasks_count, last_passed_count, last_failed_count.

3. **Документация**  
   - `docs/last-attempt-statistics-stage6.md` — что считается основным результатом, разница last vs best/avg, где применено.  
   - `docs/api-reference.md` — в разделе «Эндпойнты статистики» добавлено описание last-based и примеры новых полей.  
   - `docs/learning-engine-v1-implementation-plan.md` — этап 6 отмечен как done.

4. **Тесты**  
   - `tests/test_last_attempt_stage6.py`: _is_pass, PASS_THRESHOLD_RATIO, наличие last-based и дополнительных полей в get_stats_by_user, get_stats_by_course, get_stats_by_task.

Полный diff: [2026-02-25-last-attempt-stage6.diff](2026-02-25-last-attempt-stage6.diff)

```diff
--- a/app/services/learning_engine_service.py
+++ b/app/services/learning_engine_service.py
@@ -204,14 +204,22 @@ class LearningEngineService:
-        with_result_stmt = text("""
-            SELECT COUNT(DISTINCT tr.task_id)
+        tasks_with_last_pass_stmt = text("""
+            WITH last_per_task AS (
+                SELECT DISTINCT ON (tr.task_id)
+                    tr.task_id, tr.score AS last_score, tr.max_score AS last_max
+                ...
+            SELECT COUNT(*) FROM last_per_task
+            WHERE last_max > 0 AND (last_score::float / last_max) >= :pass_threshold
```
