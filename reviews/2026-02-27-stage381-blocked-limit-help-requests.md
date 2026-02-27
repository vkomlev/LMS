# Review: этап 3.8.1 — авто-заявки при BLOCKED_LIMIT и типизация help-requests

**Дата:** 2026-02-27  
**Контекст:** Реализация ТЗ `docs/tz-learning-engine-stage3-8-1-blocked-limit-help-requests.md`: автосоздание/переиспользование заявки при BLOCKED_LIMIT, поля request_type/auto_created/context, фильтр по типу, интеграция в next-item и task state.

## Изменённые файлы (кратко)

- `app/api/v1/learning.py` — вызов `get_or_create_blocked_limit_help_request` при `blocked_limit` (next-item) и при `BLOCKED_LIMIT` (task state)
- `app/api/v1/teacher_help_requests.py` — query-параметр `request_type`, валидация, передача в list
- `app/schemas/teacher_help_requests.py` — поля `request_type`, `auto_created`, `context` в list/detail
- `app/services/help_requests_service.py` — убран вызов `record_help_request_opened` для blocked_limit; добавлен фильтр и поля в list/detail; импорт `json` в начало файла
- `docs/api-reference.md`, `docs/smoke-learning-engine-stage3-8-help-requests.md` — описание фильтра, полей и сценария auto-create
- `tests/test_teacher_help_requests_stage381.py` — новые тесты (422, list/detail поля, фильтр request_type, backward compat)

## Начало diff

```diff
diff --git a/app/api/v1/learning.py b/app/api/v1/learning.py
--- a/app/api/v1/learning.py
+++ b/app/api/v1/learning.py
@@ -29,7 +29,10 @@ from app.services.learning_events_service import (
-from app.services.help_requests_service import get_or_create_help_request
+from app.services.help_requests_service import (
+    get_or_create_help_request,
+    get_or_create_blocked_limit_help_request,
+)
...
+    if result.type == "blocked_limit" and result.task_id is not None:
+        state = await learning_service.compute_task_state(db, student_id, result.task_id)
+        await get_or_create_blocked_limit_help_request(...)
```

Полный diff: [reviews/2026-02-27-stage381-blocked-limit-help-requests.diff](2026-02-27-stage381-blocked-limit-help-requests.diff)
