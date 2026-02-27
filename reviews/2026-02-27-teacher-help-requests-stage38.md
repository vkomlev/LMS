# Review: Learning Engine V1, этап 3.8 — Teacher help-requests

**Дата:** 2026-02-27

**Контекст:** Реализация по ТЗ [tz-learning-engine-stage3-8-teacher-help-requests.md](../docs/tz-learning-engine-stage3-8-teacher-help-requests.md): заявки на помощь студентов, API для преподавателя/методиста (список, карточка, закрытие, ответ), ACL, идемпотентность close/reply, интеграция с request-help и messages.

**Изменённые файлы (git diff):**
- `app/api/main.py` — подключён роутер `teacher_help_requests_router`
- `app/api/v1/learning.py` — после `record_help_requested` вызов `get_or_create_help_request`, в ответ добавлен `request_id`
- `app/db/base.py` — импорты моделей `help_requests`, `help_request_replies`
- `app/schemas/learning_api.py` — в `RequestHelpResponse` добавлено опциональное поле `request_id`
- `app/services/learning_events_service.py` — аудит-события `record_help_request_opened`, `record_help_request_closed`, `record_help_request_replied`
- `docs/api-reference.md` — таблица Learning API дополнена эндпоинтами teacher/help-requests, ссылка на smoke
- `tests/test_learning_api_routes.py` — в ожидаемые пути добавлены маршруты teacher/help-requests

**Новые файлы (не в diff):**
- `app/db/migrations/versions/20260227_100000_help_requests_stage38.py` — таблицы `help_requests`, `help_request_replies`, индексы, CHECK
- `app/models/help_requests.py`, `app/models/help_request_replies.py`
- `app/schemas/teacher_help_requests.py` — схемы list/detail/close/reply
- `app/services/help_requests_service.py` — CRUD, ACL, назначение teacher, close, reply (MessagesService)
- `app/api/v1/teacher_help_requests.py` — GET list, GET by id, POST close, POST reply
- `tests/test_teacher_help_requests_stage38.py` — тесты по ТЗ (request_id, status=open, ACL 403, close идемпотентность, reply + dedupe, close_after_reply)
- `docs/smoke-learning-engine-stage3-8-help-requests.md` — сценарий create → list → detail → reply → close → list(open)=empty

Полный diff изменённых файлов: [2026-02-27-teacher-help-requests-stage38.diff](2026-02-27-teacher-help-requests-stage38.diff)
