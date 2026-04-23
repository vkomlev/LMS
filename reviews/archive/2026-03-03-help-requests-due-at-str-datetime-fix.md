# Fix: TypeError при сравнении due_at с now (str vs datetime)

**Дата:** 2026-03-03  
**Контекст:** В `GET /api/v1/teacher/help-requests/:id` при формировании карточки заявки возникала ошибка `'<' not supported between instances of 'str' and 'datetime.datetime'`: значение `due_at` из сырого SQL приходило строкой, сравнение с `datetime.now(timezone.utc)` падало. Аналогичный риск был в списке заявок.

**Изменения:** Введена нормализация `due_at` из сырого SQL (строка или datetime) в timezone-aware datetime; используется для расчёта `is_overdue` и для поля `due_at` в ответе (схема ожидает `Optional[datetime]`).

Начало diff:

```diff
--- a/app/services/help_requests_service.py
+++ b/app/services/help_requests_service.py
@@ -32,6 +32,22 @@ from app.services.teacher_courses_service import TeacherCoursesService
 logger = logging.getLogger(__name__)
 
 
+def _normalize_due_at(due_at: Any) -> Optional[datetime]:
+    """Приводит due_at из сырого SQL (str или datetime) к timezone-aware datetime для сравнения с now."""
+    ...
+
 def _task_title_display(task_id: int, external_uid: Optional[str]) -> str:
```

Полный diff: [2026-03-03-help-requests-due-at-str-datetime-fix.diff](2026-03-03-help-requests-due-at-str-datetime-fix.diff)
