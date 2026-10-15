# tsk-994 (П5): итоговый срез после курса

Постановка: `docs/briefs/2026-10-14-p3-p5-retrieval-help-gate-final-check.md`, Фаза C (шаги 13–17).
Решение оператора Р4: срез — только диагностика, статус курса, зависимости и выпуск не меняются.

- Рубильник `courses.final_check_config` (`{"enabled": false, "size": 10}`), `GET/PUT /courses/{id}/final-check`.
- Таблица `final_check_runs`, миграция `tsk994_final_check` (down: `tsk992_task_review_schedule`).
- `app/services/final_check_service.py`: выборка (сид с солью «final», по главам), гейт COMPLETED, ответы `source_system='final_check'`, итог, ошибки → `retrieval_service.schedule_forgotten`.
- Единое правило «вне учебного пути»: `learning_gaps_service.NON_LEARNING_PATH_SOURCES`.
- Пометка `is_final_check` в истории ученика и «проверил в итоговом срезе» в ленте преподавателя.

Дифф: `reviews/2026-10-15-tsk994-final-check.diff`. Ревью: `reviews/2026-10-15-tsk994-final-check-review.md`.

```diff
diff --git a/app/api/v1/attempts.py b/app/api/v1/attempts.py
index 89f21a7..8671fff 100644
--- a/app/api/v1/attempts.py
+++ b/app/api/v1/attempts.py
@@ -76,7 +76,7 @@ from app.services.attempt_attachments import (
 from app.services.learning_engine_service import LearningEngineService
 from app.services import dependency_lock_service
 from app.services import retrieval_service
-from app.services.learning_gaps_service import RETRIEVAL_SOURCE
+from app.services.learning_gaps_service import learning_path_results_clause
 from app.services.tasks_acl_service import (
     assert_task_access,
     assert_task_active_for_student,
@@ -217,11 +217,12 @@ async def _build_attempt_with_results(
     Здесь специально не выносим в сервис, чтобы минимально трогать доменную логику,
     как ты просил — «нет только самих эндпойнтов».
     """
-    # tsk-992: сдача повтора лежит в курсовой попытке, но к её прохождению
-    # не относится — ни в список результатов, ни в суммы баллов не входит.
+    # tsk-992/tsk-994: сдачи повтора и итогового среза лежат в курсовой
+    # попытке, но к её прохождению не относятся — ни в список результатов, ни
+    # в суммы баллов не входят.
     stmt = select(TaskResults).where(
         TaskResults.attempt_id == attempt.id,
-        TaskResults.source_system.is_distinct_from(RETRIEVAL_SOURCE),
+        learning_path_results_clause(TaskResults.source_system),
     )
     result = await db.execute(stmt)
     rows: List[TaskResults] = result.scalars().all()
diff --git a/app/api/v1/courses_extra.py b/app/api/v1/courses_extra.py
index 5984876..dc48848 100644
--- a/app/api/v1/courses_extra.py
+++ b/app/api/v1/courses_extra.py
@@ -23,6 +23,7 @@ from app.schemas.courses import (
 )
 from app.schemas.course_parents import CourseParentOrderUpdate
 from app.schemas.retrieval import CourseRetrievalConfig
+from app.schemas.final_check import CourseFinalCheckConfig
 from app.schemas.course_sampling import (
     CourseSamplingSettingsRequest,
     CourseSamplingSettingsResponse,
@@ -1466,3 +1467,83 @@ async def put_course_retrieval_endpoint(
         course_id, current_user.id, config,
     )
     return result
+
+
+# ---------------------------------------------------------------------------
+# tsk-994 (П5): рубильник итогового среза на корневом курсе
+# (по образцу /courses/{id}/retrieval; поле courses.final_check_config)
+# ---------------------------------------------------------------------------
+
+
+@router.get(
+    "/courses/{course_id}/final-check",
+    response_model=CourseFinalCheckConfig,
+    summary="Рубильник итогового среза на курсе (tsk-994)",
+    description=(
+        "`{enabled, size}` из `courses.final_check_config`. Пустое поле читается "
+        "как `enabled=false, size=10`. Действует на КОРНЕВОМ курсе."
```
