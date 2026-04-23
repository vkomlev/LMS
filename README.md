# LMS Core API

Backend для системы управления обучением (LMS): курсы, материалы, задачи, проверка, роли, запросы помощи. REST-API на FastAPI + PostgreSQL.

## Что внутри

- Управление пользователями и множественными ролями (студенты, преподаватели, методисты, админы)
- Иерархические курсы, зависимости, связки студент↔курс и преподаватель↔курс
- Учебные материалы (text, video, link, pdf, script, document) + импорт из Google Sheets
- Задачи-квизы с попытками, подсказками, автопроверкой и статистикой
- Запросы помощи от учеников (с типизацией и контекстом)
- Часть бизнес-логики — в БД через триггеры (см. [docs/database-triggers-contract.md](docs/database-triggers-contract.md))

## Быстрый старт

Требования: Python 3.10+, PostgreSQL 12+, pip.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate

pip install -r requirements.txt
```

Создать `.env` в корне:

```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/Learn
VALID_API_KEYS=bot-key-1,admin-key-1
LOG_LEVEL=INFO
```

Применить миграции и запустить:

```bash
alembic upgrade head
python run.py
```

Сервер поднимается на `http://localhost:8000`. Интерактивная спека: `/docs` (Swagger) и `/redoc`.

## Проверка работоспособности

```bash
curl http://localhost:8000/health
curl "http://localhost:8000/api/v1/users/?api_key=bot-key-1"
```

## Структура проекта

```
LMS/
├── app/
│   ├── api/v1/          # FastAPI роутеры
│   ├── services/        # Бизнес-логика
│   ├── repos/           # Доступ к БД
│   ├── models/          # SQLAlchemy ORM
│   ├── schemas/         # Pydantic DTO
│   ├── db/migrations/   # Alembic миграции
│   ├── core/            # logger.py, конфигурация
│   ├── auth/            # api_key_scheme.py
│   └── utils/           # DomainError, pagination, email
├── docs/                # Документация (API-справочники, архив)
├── reviews/             # Review-артефакты по задачам
├── tests/               # Smoke и pytest
├── alembic.ini
├── requirements.txt
├── run.py               # uvicorn entry point
├── CONTRIBUTING.md      # Правила разработки
├── AGENTS.md            # Скиллы для AI-агентов
└── README.md
```

## Документация

### API-справочники

| Раздел | Файлы |
|---|---|
| Пользователи | [API_TEACHERS_MANAGEMENT](docs/API_TEACHERS_MANAGEMENT.md), [API_STUDENTS_MANAGEMENT](docs/API_STUDENTS_MANAGEMENT.md), [API_STUDENTS_QUICK_REFERENCE](docs/API_STUDENTS_QUICK_REFERENCE.md) |
| Курсы | [courses-api](docs/courses-api.md), [courses-import-manual](docs/courses-import-manual.md), [API_TEACHER_COURSES](docs/API_TEACHER_COURSES.md) |
| Задания и проверка | [api-reference](docs/api-reference.md), [api-examples](docs/api-examples.md), [assignments-and-results-api](docs/assignments-and-results-api.md) |
| Импорт задач | [import-quick-start](docs/import-quick-start.md), [import-api-documentation](docs/import-api-documentation.md) |
| Контракты | [learning-engine-next-item](docs/learning-engine-next-item.md), [frontend-contract-sa-com](docs/frontend-contract-sa-com.md), [roles-and-api-contract](docs/roles-and-api-contract.md) |
| Технические | [database-triggers-contract](docs/database-triggers-contract.md), [openapi.json](docs/openapi.json), [CONTRIBUTING](CONTRIBUTING.md) |

### AI-слой (для Claude Code и других агентов)

Точка входа — [docs/ai/INDEX.md](docs/ai/INDEX.md). Содержит: архитектуру, модель данных, глоссарий, контракт агентов, project overrides, журнал ошибок, workflows (feature / bugfix / db-change).

### Архив
Исторические ТЗ, smoke-результаты, стадии и старые ревью — в [docs/archive/](docs/archive/) и [reviews/archive/](reviews/archive/).

## Конфигурация

| Переменная | Обязательная | Назначение |
|---|---|---|
| `DATABASE_URL` | Да | `postgresql+asyncpg://user:pass@host:5432/Learn` |
| `VALID_API_KEYS` | Да | CSV-список API-ключей для доступа |
| `LOG_LEVEL` | Нет | `DEBUG`/`INFO`/`WARNING`/`ERROR` (default `INFO`) |
| `MESSAGES_UPLOAD_DIR` | Нет | Директория для вложений сообщений |
| `MAX_ATTACHMENT_SIZE_BYTES` | Нет | Лимит вложений (байт) |

Секреты (service-account для Google Sheets) — только в `secrets/` и `.env`, никогда в коде и коммитах.

## Аутентификация

API-ключ передаётся в query-параметре `api_key`:

```bash
GET /api/v1/users/?api_key=bot-key-1
```

Валидные ключи — `VALID_API_KEYS`.

## Миграции

```bash
# Создать
alembic revision --autogenerate -m "описание"

# Применить все
alembic upgrade head

# Откатить один шаг
alembic downgrade -1
```

Правило: любое изменение схемы — через Alembic. Триггерная логика — только в миграциях, не в сервисах.

## Тесты

```bash
pytest tests/
```

Smoke-сценарии по эндпойнтам — в `tests/` (`.ps1` для PowerShell и `.py` для pytest).

## Разработка

Процесс и правила — в [CONTRIBUTING.md](CONTRIBUTING.md). Для AI-агентов — [docs/ai/AGENTS.md](docs/ai/AGENTS.md) + [.claude/CLAUDE.md](.claude/CLAUDE.md).

## Автор

Виктор Комлев
