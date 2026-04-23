# Review: Stage 3.9 — Teacher Next Modes

**Дата:** 2026-03-01  
**Контекст:** Реализация ТЗ Learning Engine V1, этап 3.9 — claim-next/release для help-requests и manual review, workload, приоритет/SLA в списке заявок, опциональный lock_token в close/reply/manual-check.

## Изменённые файлы (кратко)
- Миграция: `app/db/migrations/versions/20260301_100000_teacher_next_modes_stage39.py` (help_requests: priority, due_at, claim; task_results: review_claim).
- Модели: `app/models/help_requests.py`, `app/models/task_results.py`.
- Схемы: `app/schemas/teacher_next_modes.py` (новый), `app/schemas/teacher_help_requests.py` (priority, due_at, is_overdue, lock_token).
- Сервисы: `app/services/teacher_queue_service.py` (новый), `app/services/help_requests_service.py` (sort, priority/due_at в list/detail, check_help_request_lock, lock_token в close/reply).
- API: `app/api/v1/teacher_help_requests.py` (claim-next, release, sort), `app/api/v1/teacher_reviews.py` (новый), `app/api/v1/teacher_workload.py` (новый), `app/api/v1/task_results_extra.py` (lock_token и сброс claim в manual-check), `app/api/main.py` (подключение роутеров).
- Документация: `docs/api-reference.md`, `docs/smoke-learning-engine-stage3-9-next-modes.md`.
- Smoke: `scripts/smoke_learning_engine_stage39_next_modes.ps1`.
- Тесты: `tests/test_teacher_next_modes_stage39.py`.

## Начало diff

```diff
diff --git a/app/api/main.py b/app/api/main.py
index 3c8bf04..ea035d7 100644
--- a/app/api/main.py
+++ b/app/api/main.py
@@ -30,6 +30,8 @@ from app.api.v1.task_results_extra import router as task_results_extra_router
 from app.api.v1.learning import router as learning_router
 from app.api.v1.teacher_learning import router as teacher_learning_router
 from app.api.v1.teacher_help_requests import router as teacher_help_requests_router
+from app.api.v1.teacher_reviews import router as teacher_reviews_router
+from app.api.v1.teacher_workload import router as teacher_workload_router
 ...
```

Полный diff: [2026-03-01-stage39-teacher-next-modes.diff](2026-03-01-stage39-teacher-next-modes.diff).

## Валидация
- `alembic upgrade head` — применена ревизия teacher_next_modes_stage39.
- `python tests/test_teacher_next_modes_stage39.py` — все тесты PASS.
