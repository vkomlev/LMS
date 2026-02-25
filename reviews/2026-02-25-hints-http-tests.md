# Review: HTTP-интеграционные тесты для Hints (этап 5)

**Дата:** 2026-02-25

**Контекст:** По рекомендации добавлены 1–2 HTTP-интеграционных теста для этапа 5 (Hints): проверка, что GET `/api/v1/tasks/{id}` и list GET `/api/v1/tasks/` возвращают в ответах поля `hints_text`, `hints_video`, `has_hints`.

**Изменения:**
- В **tests/test_hints_stage5.py** добавлен интеграционный блок на httpx + ASGITransport: один event loop на оба запроса (избежание падения с async SQLAlchemy при повторном asyncio.run). Проверки: GET by id и GET list возвращают нужные поля и типы.
- В **requirements.txt** добавлена зависимость `httpx>=0.27.0` для запуска HTTP-тестов.
- В **docs/hints-stage5.md** добавлена секция «Тесты» с указанием на интеграционные проверки по HTTP.

Полный diff: [2026-02-25-hints-http-tests.diff](2026-02-25-hints-http-tests.diff)

```diff
--- a/tests/test_hints_stage5.py
+++ b/tests/test_hints_stage5.py
@@ -20,7 +20,12 @@
 from dotenv import load_dotenv
 load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")
 
-from app.schemas.tasks import TaskRead, extract_hints_from_task_content
+try:
+    import asyncio
+    import httpx
+    from httpx import ASGITransport
+    _HAS_HTTPX = True
+except Exception:
+    _HAS_HTTPX = False
 ...
+def test_http_tasks_hints_integration():
+    """HTTP: GET /tasks/{id} и GET /tasks/ возвращают hints_text, hints_video, has_hints."""
```
