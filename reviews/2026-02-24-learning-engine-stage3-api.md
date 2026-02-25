# Review: Learning Engine этап 3 — Learning API

**Дата:** 2026-02-24

**Контекст:** Внедрение Learning API по ТЗ этапа 3: 6 эндпоинтов, Pydantic-схемы, сервис событий, подключение роутеров.

**Добавлено:**
- `app/schemas/learning_api.py` — запросы/ответы для next-item, material complete, start-or-get-attempt, task state, request-help, teacher override.
- `app/services/learning_events_service.py` — запись в `learning_events` (help_requested с дедупом 5 мин, task_limit_override), идемпотентный upsert `student_material_progress` (material complete).
- `app/api/v1/learning.py` — GET next-item, POST materials/{id}/complete, POST tasks/{id}/start-or-get-attempt, GET tasks/{id}/state, POST tasks/{id}/request-help.
- `app/api/v1/teacher_learning.py` — POST teacher/task-limits/override (upsert `student_task_limit_override` + событие).
- Подключение в `app/api/main.py`: learning_router, teacher_learning_router.
- `tests/test_learning_api_routes.py` — проверка регистрации маршрутов в OpenAPI.
- `docs/smoke-learning-api.md` — чеклист curl для smoke (в рабочей копии; docs могут быть в .gitignore).
- В `docs/learning-engine-v1-implementation-plan.md` этап 3 отмечен как **done** (файл в .gitignore).

**Ошибки:** 404 по студенту/заданию/материалу/updated_by; commit на уровне эндпоинта; идемпотентность соблюдена.

Полный diff (только этап 3): [2026-02-24-learning-engine-stage3-api.diff](./2026-02-24-learning-engine-stage3-api.diff)
