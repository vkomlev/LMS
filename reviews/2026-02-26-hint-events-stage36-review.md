# Review: Hint events этап 3.6 — правки по ревью

**Дата:** 2026-02-26

**Контекст:** Закрытие замечаний по этапу 3.6 (hint-events): P1 — запрет приёмa событий для завершённой/отменённой попытки; P2 — расширение HTTP-тестов (200+дедуп, 404, 422, by-course); P2 — миграция с индексами для подсчёта `hint_open`.

**Изменения:**
- `app/api/v1/learning.py`: проверка `finished_at`/`cancelled_at` → 409; описание 409 в responses.
- `tests/test_hint_events_stage36.py`: тесты HTTP 200+дедуп, 404, 422, by-course поля hint.
- `app/db/migrations/versions/20260226_210000_learning_events_hint_open_index.py`: частичные индексы по `learning_events` для `event_type='hint_open'` (task_id из payload, student_id).

Начало diff:

```diff
diff --git a/app/api/v1/learning.py b/app/api/v1/learning.py
index 282e330..3209289 100644
--- a/app/api/v1/learning.py
+++ b/app/api/v1/learning.py
@@ -20,9 +20,15 @@ from app.schemas.learning_api import (
     TaskStateResponse,
     RequestHelpRequest,
     RequestHelpResponse,
+    HintEventRequest,
+    HintEventResponse,
 )
 ...
+    if attempt.finished_at is not None or attempt.cancelled_at is not None:
+        raise HTTPException(
+            status_code=status.HTTP_409_CONFLICT,
+            detail="Попытка уже завершена или отменена. События подсказок принимаются только для активной попытки.",
+        )
```

Полный diff: [2026-02-26-hint-events-stage36-review.diff](2026-02-26-hint-events-stage36-review.diff)
