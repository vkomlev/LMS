# Data Model — LMS Core API

## База
- СУБД: PostgreSQL
- Локальная БД: `Learn`
- Подключение: асинхронно через `asyncpg` (`DATABASE_URL=postgresql+asyncpg://...`)
- Схема-менеджмент: **только Alembic**. Прямые DDL в прод-процессе запрещены.

## Ключевые сущности

Файлы моделей — в `app/models/`. Основные таблицы (без полного списка колонок):

| Модель | Файл | Назначение |
|---|---|---|
| `User` | `models/users.py` | Пользователь; может иметь несколько ролей |
| `Role` | `models/roles.py` | Роль (student / teacher / methodist / admin и др.), русские + английские имена |
| `UserRole` (M2M) | `models/users.py` / `repos/user_roles.py` | Связка user ↔ role |
| `Course` | `models/courses.py` | Курс; поддержка иерархии (M2M `course_parents`) и зависимостей |
| `CourseParent` | `models/association_tables.py` | M2M parent↔child курсов с `order_number` |
| `CourseDependency` | — | Жёсткие зависимости между курсами (без самоссылок) |
| `UserCourse` | `models/user_courses.py` | Связка student↔course с авто-`order_number` (триггер) |
| `TeacherCourse` | — | Связка teacher↔course; синхронизация дочерних курсов |
| `StudentTeacherLink` | `repos/student_teacher_links_repository.py` | Прикрепление студента к преподавателю |
| `Material` | `models/materials.py` | Учебный материал курса; типы: text, video, link, pdf, script, document |
| `Task` | `models/tasks.py` | Задача (quiz) с solution-правилами |
| `MetaTask` | `schemas/meta_tasks.py` | Обёртки над задачами |
| `Attempt` | `models/attempts.py` (repo) | Попытка решения задачи студентом |
| `TaskResult` | `models/task_results.py` | Итоговый результат по задаче |
| `HelpRequest` | `models/help_requests.py` | Запрос помощи от ученика, типы / context |
| `HelpRequestReply` | `models/help_request_replies.py` | Ответ преподавателя |
| `Achievement` | `models/achievements.py` | Каталог достижений |
| `UserAchievement` | `models/user_achievements.py` | Привязка user↔achievement |
| `Message` | `models/messages.py` | Личные сообщения (с вложениями, threads) |
| `Notification` | `models/notifications.py` | Уведомления |
| `SocialPost` | `models/social_posts.py` | Социальные посты |
| `AccessRequest` | `models/access_requests.py` | Заявки на доступ к ролям |
| `DifficultyLevel` | `repos/difficulty_levels_repo.py` | Уровни сложности (UID) |

## Бизнес-логика в триггерах и constraints

Источник истины: [database-triggers-contract.md](../database-triggers-contract.md). Дублировать логику в сервисах запрещено.

Ключевые миграции (хронология):

| Миграция | Содержание |
|---|---|
| `20250101_000000_add_courses_triggers` | Базовые триггеры курсов |
| `20260124_175541_migrate_course_parents_to_many_to_many` | Переход parent→M2M |
| `20260124_190000_add_order_number_to_course_parents` | `order_number` для иерархии |
| `20260126_120000_add_teacher_courses_table_and_triggers` | Teacher↔course + синхронизация дочерних |
| `20260127_230000_remove_auto_link_triggers_and_add_parent_check` | Снятие авто-линков, parent-check |
| `20260129_100000_materials_structure_and_triggers` | Структура материалов |
| `20260129_140000_add_script_and_document_material_types` | Типы script, document |
| `20260205_140000_fix_materials_delete_trigger` | Фикс удаления материалов |
| `20260216_100000_add_difficulties_uid` | UID для уровней сложности |
| `20260225_100000_learning_engine_stage1_db_foundation` | Learning engine: базовые таблицы |
| `20260225_110000_learning_engine_stage1_check_constraints` | Check-constraints |
| `20260226_100000_attempts_cancel_stage35` | Отмена попыток |
| `20260226_210000_learning_events_hint_open_index` | Индекс для hint-событий |
| `20260227_100000_help_requests_stage38` | Запросы помощи от учеников |
| `20260227_120000_help_requests_type_and_context_stage381` | Типизация + context |
| `20260301_100000_teacher_next_modes_stage39` | Режимы выдачи заданий преподавателем |

## Date/Time safety (критично)

Корневой инцидент: сравнение `str` из `text(...)` с `datetime` → `TypeError`. Правила:

- Raw SQL через `text(...)` возвращающий дата/время — нормализовать через helper до сравнения
- Explicit type-guards в сервисе перед SLA/TTL-сравнениями
- Naive `datetime` — reject или normalize по проектному правилу
- Обязательные negative tests: `str`, naive `datetime`, `None`

Подробности — [PROJECT_OVERRIDES.md](PROJECT_OVERRIDES.md) и `.claude/CLAUDE.md` (секция Date/Time Safety).

## Read-контракты

OpenAPI-спека: [docs/openapi.json](../openapi.json) (снимок). Live-спека — на `/docs` и `/redoc` при запущенном сервере.

Подробные контракты — в `docs/API_*.md` (маршрут через [README.md](../../README.md)).

## Как безопасно смотреть данные

- MCP PostgreSQL (алиас `postgresql`) в read-only — схема, data diagnostics
- Write-запросы к БД — только при явном требовании задачи, с review-gate
- Любые изменения схемы — только через Alembic миграцию
