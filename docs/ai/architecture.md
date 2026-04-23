# Architecture — LMS Core API

## Стек
- Python 3.10+
- FastAPI 0.115 + Uvicorn
- SQLAlchemy 2.0 (async, asyncpg)
- PostgreSQL (локально БД `Learn`)
- Alembic — миграции
- Pydantic 2.x — валидация
- Google Sheets API — импорт материалов/задач/курсов

## Точки входа

| Артефакт | Назначение |
|---|---|
| `run.py` | Локальный запуск uvicorn на `0.0.0.0:8000` |
| `app/api/main.py` | FastAPI-приложение, регистрация роутеров, exception handlers |
| `alembic.ini` + `app/db/migrations/` | Миграции схемы БД |

## Слои

Строгий порядок: **API → Services → Repos → Models**.

```
app/
├── api/v1/           # FastAPI роутеры (эндпойнты)
├── services/         # Бизнес-логика
├── repos/            # Доступ к БД (SQLAlchemy)
├── models/           # SQLAlchemy ORM-модели
├── schemas/          # Pydantic DTO (request/response)
├── db/               # session.py, migrations/
├── core/             # logger.py, конфигурация
├── auth/             # api_key_scheme.py
└── utils/            # exceptions (DomainError), pagination, email, security
```

**Правило**: бизнес-логика — только в `services/`. Модели и репозитории — без решений, только структура и доступ.

## Регистрация роутеров

`app/api/main.py` подключает два типа роутеров:

1. **CRUD-генератор** (`app/api/v1/crud.py::create_crud_router`) — универсальный CRUD по `{Schema, Service}` для: users, achievements, courses, difficulty-levels, roles, materials, messages, notifications, social-posts, tasks, task-results
2. **Extra-роутеры** (специфичные эндпойнты) — регистрируются **до** CRUD, чтобы не перехватывались шаблоном `/{item_id}`:
   - `courses_extra`, `materials_extra`, `messages_extra`, `tasks_extra`, `task_results_extra`, `user_courses_extra`
   - Доменные роутеры: `users`, `user_achievements`, `user_courses`, `user_roles`, `student_teacher_links`, `teacher_courses`, `course_dependencies`, `access_requests`, `meta_tasks`, `checking`, `attempts`, `learning`, `teacher_learning`, `teacher_workload`, `teacher_help_requests`, `teacher_reviews`

Префикс всех путей: `/api/v1`. Health-check: `GET /health`.

## Обработка ошибок

`app/utils/exceptions.py::DomainError` — базовый класс доменных ошибок. Handler в `main.py` возвращает:

```json
{"error": "domain_error", "detail": "...", "payload": {...}}
```

Сервис бросает `DomainError` с нужным `status_code`. `IntegrityError` от триггеров БД преобразуется в `DomainError` в сервисном слое.

`RequestValidationError` (422) — логируется с контекстом; для `/attempts/*/answers` дополнительно парсится `answer.type` для observability.

## Логирование

- `app/core/logger.py::setup_logging()` — конфиг: файл `logs/app.log` + консоль
- Уровень — `LOG_LEVEL` из `.env`
- Для debug/triage: `skills/core/fastapi-api-developer/scripts/log_triage.py`

## Аутентификация

- Query-параметр `api_key` (см. `app/auth/api_key_scheme.py`)
- Валидные ключи — `VALID_API_KEYS` в `.env` (CSV)
- Ролевая модель — в БД (таблицы `roles`, `user_roles`, `access_requests`)

## Бизнес-логика в БД

Часть логики реализована триггерами и check-constraints. Подробности — в [docs/database-triggers-contract.md](../database-triggers-contract.md). Дублировать триггерную логику в сервисах **запрещено**.

Реализовано в БД:
- Автонумерация `order_number` в `user_courses`
- Пересчёт `order_number` после удаления
- Валидация циклов в иерархии курсов
- Запрет самоссылок в `course_dependencies`
- Синхронизация `teacher_courses` для дочерних курсов (и её снятие — см. миграцию `20260127_230000`)
- Структура и удаление материалов курса (см. `20260129_100000`, `20260205_140000`)

## Интеграции

- **Google Sheets** (`app/services/google_sheets_service.py`) — импорт курсов, материалов, задач через service-account JSON в `secrets/`
- **MCP PostgreSQL** (dev-only) — read-only SQL-проверки схемы и данных из агентов; алиас `postgresql`

## Потоки данных (основные)

1. **HTTP-запрос** → API-роутер → Pydantic-валидация → Service → Repo → SQLAlchemy → PostgreSQL
2. **Ошибка домена** → `DomainError` → handler → JSON с `status_code`
3. **Триггер БД** → `IntegrityError` → сервис ловит → преобразует в `DomainError`
4. **Импорт из Google Sheets** → service-account auth → parser service → bulk upsert через repo
