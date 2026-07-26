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
| `20241231_235959_baseline_pre_alembic_schema` | Baseline: 18 таблиц, поднятых до начала трекинга Alembic |
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
| `20260428_010000_M1_users_relax_constraints` | Y-1: снятие `NOT NULL` password_hash/email, `pgcrypto` |
| `20260428_020000_M2_identity_link` | Y-1: таблица `identity_link` (multi-identity email/tg/vk) |
| `20260428_030000_M3_user_session_magic_link` | Y-1: таблицы `user_session` + `magic_link` |
| `20260428_040000_M4_audit_product_events` | Y-1: `audit_event` (append-only) + `product_event` (partitioned by month) |
| `20260428_050000_M5_guest_session_attempt` | Y-1: таблицы `guest_session` + `guest_attempt` |
| `20260428_060000_M6_users_tg_id_backfill` | Y-1.5: бэкфилл `users.tg_id` ↔ `identity_link` (kind='tg') |
| `20260429_010000_M7_task_results_user_received_idx` | Y-3: индекс `task_results(user_id, received_at DESC)` для streak-запроса |
| `20260430_010000_M8_notifications_inbox` | Y-4: расширение `notifications` под inbox-семантику |
| `20260430_020000_M9_zombie_sanitize` | Y-4.2: data-миграция — санация zombie `task_results` (R-3 fix) |
| `20260501_010000_M10_role_backfill` | Y-4 pre-S5: бэкфилл роли `student` для users без роли |
| `20260502_010000_M11_courses_is_public_demo` | Y-5: `courses.is_public_demo` для guest-mode |
| `20260504_010000_M12_y6_optimistic_pass` | Y-6: optimistic-PASSED бэкфилл + индекс pending review |
| `20260521_120000_tasks_order_position_triggers` | `tasks.order_position` — колонка, бэкфилл, триггеры, индекс (зеркало materials) |
| `20260606_010000_tsk111_content_requirement_skip` | tsk-111: уровни content requirement + skip progress |
| `20260624_010000_tsk031_assignment_rules` | tsk-031: `assignment_rule` + `assignment_event` — авто/ручное назначение курсов |
| `20260627_010000_tsk122_quiz_scale_scores` | tsk-122 Stage 1: `task_results.scale_scores` (JSONB) для квиз-шкал SC_Qw/MC_Qw |
| `20260627_020000_tsk122_trigger_quiz_scale` | tsk-122 Stage 2: значение `quiz_scale` в CHECK `assignment_rule_trigger_event_check` |
| `20260717_010000_tsk264_attempts_root_course` | tsk-264: `attempts.root_course_id` — контекст навигации, попытки по паре «курс + задание» |
| `20260726_010000_tsk428_lesson_calendar_stage1` | tsk-428 (Календарь LMS Фаза 1): `operating_hours` + `lesson_slot` + `lesson_occurrence` + `attendance_event` — 4 новые таблицы, ноль изменений в существующих |

## Date/Time safety (критично)

Корневой инцидент: сравнение `str` из `text(...)` с `datetime` → `TypeError`. Правила:

- Raw SQL через `text(...)` возвращающий дата/время — нормализовать через helper до сравнения
- Explicit type-guards в сервисе перед SLA/TTL-сравнениями
- Naive `datetime` — reject или normalize по проектному правилу
- Обязательные negative tests: `str`, naive `datetime`, `None`

Подробности — [PROJECT_OVERRIDES.md](PROJECT_OVERRIDES.md) и `.claude/CLAUDE.md` (секция Date/Time Safety).

## Phase Y-1 (применено) — миграции M1-M5

Миграции M1–M5 (`20260428_*`), см. таблицу выше. Down-revision: `teacher_next_modes_stage39`.

### Изменения в `users`

- `password_hash` — снять `NOT NULL` (passwordless users допустимы)
- `email` — снять `NOT NULL`; UNIQUE constraint заменяется на `partial UNIQUE INDEX WHERE email IS NOT NULL`
- `CREATE EXTENSION IF NOT EXISTS pgcrypto` (для `gen_random_uuid()`)

### Новые таблицы

| Таблица | Назначение |
|---|---|
| `identity_link` | Multi-identity: email / tg / vk. `UNIQUE(kind, value)`. Backfill из `users.tg_id` и `users.email`. VK access_token — Fernet-шифрованный. |
| `user_session` | UUID PK, `token_hash BYTEA UNIQUE`, TTL 15 мин access / 30 дней refresh, `revoked_at`. Partial index `WHERE revoked_at IS NULL`. |
| `magic_link` | Email magic-link: `token_hash BYTEA UNIQUE`, `expires_at`, `consumed_at`. TTL 15 мин, одноразовый. |
| `audit_event` | Append-only (trigger `audit_event_immutable`). `BigSerial PK`, `event_type`, `ip INET`, `details JSONB`. |
| `product_event` | RANGE partitioned by month (`ts`), 6 партиций вперёд. Funnel-аналитика. |
| `guest_session` | UUID PK, анонимный пользователь; `attributed_user_id` при регистрации. |
| `guest_attempt` | Попытки гостя; `attributed_user_id` + `attributed_at` при атрибуции. |

Детали миграций (DDL, indexes, downgrade): [docs/specs/2026-04-27-tech-spec-Y1-auth-extension.md §4](../specs/2026-04-27-tech-spec-Y1-auth-extension.md)

## Календарь LMS Фаза 1 (tsk-428, применено)

Декомпозиция tsk-021 (блокер tsk-410 «Итоги занятия»), план —
[docs/specs/2026-07-26-plan-kalendar-lms.md](../specs/2026-07-26-plan-kalendar-lms.md).
4 новые таблицы, ноль изменений в существующих моделях:

| Таблица | Назначение |
|---|---|
| `operating_hours` | Часы работы школы (общие на всю школу, не per-teacher): `weekday`(0-6)/`start_time`/`end_time`/`timezone` (DEFAULT `Europe/Moscow`) |
| `lesson_slot` | Закреплённый повторяющийся слот пары ученик-преподаватель (индивидуальный, не групповой). `is_active` — деактивация вместо удаления |
| `lesson_occurrence` | Конкретное занятие: из слота (генератор) или ad-hoc (`slot_id IS NULL`). `status`: scheduled/confirmed/declined/rescheduled/no_show/completed |
| `attendance_event` | Append-only журнал действий по посещаемости (как `audit_event`); текущий статус — денормализованная проекция на `lesson_occurrence.status` |

**Конвенция weekday:** `0=понедельник .. 6=воскресенье` (Python `date.weekday()`/ISO — НЕ cron/JS, где 0=воскресенье).

**Генератор occurrence:** `app/services/lesson_occurrence_generator_service.py::lesson_occurrence_generator_tick` — APScheduler-тик (интервал `LESSON_OCCURRENCE_CRON_INTERVAL_MIN`, default 60 мин), горизонт `LESSON_OCCURRENCE_HORIZON_DAYS` (default 14 дней), идемпотентен через `ON CONFLICT (slot_id, scheduled_at) WHERE slot_id IS NOT NULL DO NOTHING`, multi-worker-safe через `pg_try_advisory_xact_lock` (паттерн `escalation_service.py`, отдельный lock-ключ `0x4C534E43`).

**Таймзона:** MVP — захардкожен `Europe/Moscow` (без DST с 2014, UTC+3 круглый год). `users.timezone` не существует.

**Фаза 2 (tsk-429, применено):** явка ученика + напоминания + авто-no_show.
- `POST /lesson-occurrences/{id}/attendance` (`joined`→`confirmed`, `declined`→`declined`) — `require_authenticated`, ownership по `occurrence.student_id == current_user.id` (403 чужому, 404 несуществующему, 409 если статус уже закрыт: `no_show`/`completed`/`rescheduled`).
- `GET /me/lesson-occurrences?from=&to=&limit=` — занятия текущего ученика.
- Cron `lesson_attendance_cron_tick` (`app/services/lesson_attendance_cron_service.py`, интервал `LESSON_ATTENDANCE_CRON_INTERVAL_MIN` default 5 мин): reminder-ветка (`LESSON_REMINDER_LEAD_MINUTES` default 30, once-only через проверку существующей `notifications` строки) + no-show-ветка (`LESSON_NO_SHOW_THRESHOLD_MINUTES` default 10). **Важно:** no-show трогает только `status='scheduled'` — `confirmed` уже означает, что ученик подтвердил присутствие («Я на занятии»), и прошедшее время не должно задним числом это переписывать.
- Уведомления — существующий `Notifications` inbox (`kind='lesson_reminder'` ученику, `kind='lesson_missed'` ученику И преподавателю, `payload.role` различает адресата).

**Фаза 3 (tsk-430, применено, разблокирует tsk-410):** панель преподавателя, перенос, ad-hoc отработка.
- `GET /teacher/lesson-occurrences?teacher_id=&from=&to=` — занятия преподавателя с живым флагом `is_overdue` (не ждёт cron-тик; считается только для `status='scheduled'`).
- `POST /teacher/lesson-occurrences/{id}/attendance` (`manual_present`→`confirmed`, `manual_absent`→`no_show`) — в отличие от студенческого эндпоинта, заблокирован только `rescheduled` (преподаватель обязан уметь исправить ошибочный `no_show`/`completed`).
- `POST /teacher/lesson-occurrences/add-student` — создать ad-hoc occurrence вручную (`slot_id=NULL`).
- `GET /lesson-occurrences/available-slots?occurrence_id=` — кандидаты для переноса в рамках `operating_hours` без коллизий (шаг перебора 30 минут).
- `POST /lesson-occurrences/{id}/reschedule` — старый occurrence → `status=rescheduled` + `rescheduled_to_id`; создаётся новый (`slot_id=NULL`, тот же student/teacher/duration, `status=scheduled`). Без `attendance_event` — не действие явки, а смена состояния occurrence.
- `POST /lesson-occurrences/ad-hoc` — ученик сам записывается на отработку.
- Коллизии — `LessonOccurrenceRepository.has_overlap` (реальный диапазон времени `[scheduled_at, scheduled_at+duration)`, не weekday+time-of-day как у `lesson_slot`). `operating_hours` не настроены → проверка не блокирует (graceful default, см. `lesson_calendar_service.is_within_operating_hours`).

Все занятия теперь: генератор (Фаза 1) → явка/no-show (Фаза 2) → панель преподавателя/перенос/ad-hoc (Фаза 3). Фаза 4 (TG-дублирование) — опциональна, не реализована.

## Read-контракты

OpenAPI-спека: [docs/openapi.json](../openapi.json) (снимок). Live-спека — на `/docs` и `/redoc` при запущенном сервере.

Подробные контракты — в `docs/API_*.md` (маршрут через [README.md](../../README.md)).

## Как безопасно смотреть данные

- MCP PostgreSQL (алиас `postgresql`) в read-only — схема, data diagnostics
- Write-запросы к БД — только при явном требовании задачи, с review-gate
- Любые изменения схемы — только через Alembic миграцию
