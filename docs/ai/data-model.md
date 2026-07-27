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

## Календарь LMS (tsk-428/429/430/435, применено)

Декомпозиция tsk-021 (блокер tsk-410 «Итоги занятия»), план —
[docs/specs/2026-07-26-plan-kalendar-lms.md](../specs/2026-07-26-plan-kalendar-lms.md).
**tsk-435 (rework, применено 2026-07-26):** реальные данные (импорт Яндекс.Календаря
оператора) показали, что живая практика ГРУППОВАЯ (2-11 учеников на одно время с
одним преподавателем) — вразрез с исходным правилом «индивидуальное» из Фазы 1.
`lesson_slot`/`lesson_occurrence` больше НЕ хранят `student_id`/`status` напрямую —
участники и их явка вынесены в отдельные таблицы (M2M ниже). Breaking-миграция была
безопасна: на момент rework все 4 таблицы Фазы 1 были пусты и на dev, и на prod.

| Таблица | Назначение |
|---|---|
| `operating_hours` | Часы работы школы (общие на всю школу, не per-teacher): `weekday`(0-6)/`start_time`/`end_time`/`timezone` (DEFAULT `Europe/Moscow`). **Несколько окон на один weekday — норма** (tsk-436/437, напр. утро+вечер с перерывом посередине под личное время оператора) — БД без уникального ограничения на `weekday`, только запрет пересечения окон внутри одного дня (сервисная проверка, не DB constraint) |
| `lesson_slot` | Закреплённый повторяющийся ГРУППОВОЙ слот преподавателя (`teacher_id`, `weekday`, `start_time`, `duration_minutes`, `timezone`, `is_active`) — без `student_id` |
| `lesson_slot_student` | M2M участники слота: `slot_id`+`student_id`, `is_active` (мягкое удаление участника — сохраняет историю occurrence) |
| `lesson_occurrence` | Конкретное занятие: из слота (генератор) или ad-hoc (`slot_id IS NULL`) — `teacher_id`/`scheduled_at`/`duration_minutes`, БЕЗ `student_id`/`status` |
| `lesson_occurrence_participant` | Явка ОДНОГО участника ОДНОГО occurrence: `status` (scheduled/confirmed/declined/rescheduled/no_show/completed), `rescheduled_to_occurrence_id` — независимо от остальных участников той же группы |
| `attendance_event` | Append-only журнал действий по посещаемости (как `audit_event`) — НЕ менялся при rework: уже ключуется по (`occurrence_id`, `actor_user_id`), подходит для группового участника без правок |

**Конвенция weekday:** `0=понедельник .. 6=воскресенье` (Python `date.weekday()`/ISO — НЕ cron/JS, где 0=воскресенье).

**Генератор occurrence:** `app/services/lesson_occurrence_generator_service.py::lesson_occurrence_generator_tick` — APScheduler-тик (интервал `LESSON_OCCURRENCE_CRON_INTERVAL_MIN`, default 60 мин), горизонт `LESSON_OCCURRENCE_HORIZON_DAYS` (default 14 дней). Создаёт occurrence (`ON CONFLICT (slot_id, scheduled_at) DO UPDATE` no-op — нужен `RETURNING id` даже на конфликте, чтобы синхронизировать участников) и СИНХРОНИЗИРУЕТ участников из активных `lesson_slot_student` в `lesson_occurrence_participant` (`ON CONFLICT (occurrence_id, student_id) DO NOTHING`) на каждый тик — новый ученик, добавленный в слот, получает участие во всех уже сгенерированных будущих occurrence сразу (без ожидания тика — `lesson_calendar_service.add_slot_participant` бэкфиллит явно), тик лишь подхватывает то, что бэкфилл мог пропустить. Multi-worker-safe через `pg_try_advisory_xact_lock` (паттерн `escalation_service.py`, отдельный lock-ключ `0x4C534E43`).

**Таймзона:** MVP — захардкожен `Europe/Moscow` (без DST с 2014, UTC+3 круглый год). `users.timezone` не существует.

**Фаза 2 (tsk-429, применено, per-участнику после tsk-435):** явка ученика + напоминания + авто-no_show.
- `POST /lesson-occurrences/{id}/attendance` (`joined`→`confirmed`, `declined`→`declined`) — `require_authenticated`, ownership по наличию СВОЕЙ строки в `lesson_occurrence_participant` (403 если ученик не участник, 404 несуществующему occurrence, 409 если участие уже закрыто: `no_show`/`completed`/`rescheduled`).
- `GET /me/lesson-occurrences?from=&to=&limit=` — занятия текущего ученика (свой статус участия, без списка остальных участников группы — приватность).
- Cron `lesson_attendance_cron_tick` (`app/services/lesson_attendance_cron_service.py`, интервал `LESSON_ATTENDANCE_CRON_INTERVAL_MIN` default 5 мин): reminder-ветка (`LESSON_REMINDER_LEAD_MINUTES` default 30, once-only через проверку `notifications` строки по `occurrence_id` И `user_id` — важно оба, иначе в групповом occurrence напоминание первому участнику погасило бы напоминания остальным) + no-show-ветка (`LESSON_NO_SHOW_THRESHOLD_MINUTES` default 10, по каждому участнику независимо). **Важно:** no-show трогает только участника в `status='scheduled'` — `confirmed` уже означает, что ученик подтвердил присутствие («Я на занятии»), и прошедшее время не должно задним числом это переписывать.
- Уведомления — существующий `Notifications` inbox (`kind='lesson_reminder'` ученику, `kind='lesson_missed'` ученику И преподавателю, `payload.role` различает адресата).

**Фаза 3 (tsk-430, применено, разблокирует tsk-410; per-участнику после tsk-435):** панель преподавателя, перенос, ad-hoc отработка.
- `GET /teacher/lesson-occurrences?teacher_id=&from=&to=` — занятия преподавателя, каждое с полным списком участников (`participants[]`) + живой флаг `is_overdue` НА КАЖДОГО (не ждёт cron-тик; истинен только для участника в `status='scheduled'`).
- `POST /teacher/lesson-occurrences/{id}/attendance` (`{student_id, action}`, `manual_present`→`confirmed`, `manual_absent`→`no_show`) — правит ОДНОГО участника occurrence; заблокирован только `rescheduled` (преподаватель обязан уметь исправить ошибочный `no_show`/`completed`).
- `POST /teacher/lesson-occurrences/add-student` — создать ad-hoc occurrence с одним начальным участником (`slot_id=NULL`).
- `POST /teacher/lesson-occurrences/{id}/participants` — добавить ученика к УЖЕ существующему occurrence (подключить опоздавшего/новенького к идущей группе).
- `GET /lesson-occurrences/available-slots?occurrence_id=` — кандидаты для переноса СВОЕГО участия в рамках `operating_hours`, без коллизий у ЭТОГО ученика (шаг перебора 30 минут).
- `POST /lesson-occurrences/{id}/reschedule` — переносит только УЧАСТИЕ вызывающего ученика: старая строка `lesson_occurrence_participant` → `status=rescheduled` + `rescheduled_to_occurrence_id`; создаётся новый occurrence (`slot_id=NULL`, тот же teacher/duration) с новой строкой участника. Остальные участники старого группового occurrence НЕ затрагиваются. Без `attendance_event` для самого переноса — это состояние участника, не действие явки.
- `POST /lesson-occurrences/ad-hoc` — ученик сам записывается на отработку (создаёт occurrence + одного участника — себя).
- Коллизии — `LessonOccurrenceParticipantRepository.has_student_overlap` (реальный диапазон времени, ТОЛЬКО по ученику — преподаватель по design может вести несколько occurrence одновременно, это и есть группа). `LessonSlotRepository.has_overlap` (для создания слотов) остался teacher-only. `operating_hours` не настроены → проверка не блокирует (graceful default, см. `lesson_calendar_service.is_within_operating_hours`).

Все занятия теперь: генератор (Фаза 1, групповой) → явка/no-show по участнику (Фаза 2) → панель преподавателя/перенос/ad-hoc по участнику (Фаза 3). Фаза 4 (TG-дублирование) — опциональна, не реализована.

**tsk-439 (применено): авто-подтверждение явки по реальному учебному действию.**
Если у ученика прямо сейчас идёт занятие (участие ещё `scheduled`, `now` в
[`scheduled_at`, `scheduled_at+duration_minutes`)) и он совершает реальное
учебное действие — сдаёт ответ (`POST /attempts/{id}/answers`) или
завершает/пропускает материал (`POST /learning/materials/{id}/complete|skip`)
— явка подтверждается автоматически (`status → confirmed`,
`attendance_event(action='auto_joined', actor_user_id=student_id)`), без
явного клика "Я на занятии". Новый метод
`LessonOccurrenceParticipantRepository.get_current_scheduled_for_student` +
`lesson_attendance_service.auto_confirm_if_in_progress` — вызывается
soft-fail (try/except, не ломает основной поток) из обоих hook-точек.
Решение оператора (`AskUserQuestion`): «активность» = реальное учебное
действие (task_results/student_material_progress), НЕ любой page view —
иначе потребовалась бы дорогая middleware на каждый запрос. Тихий no-op вне
окна занятия (подавляющее большинство вызовов) — статус `declined`/
`rescheduled`/`no_show`/`completed`/`confirmed` не переписывается (репо-метод
фильтрует строго `status='scheduled'`).

## Совместное ведение занятий (tsk-443, применено)

Оператор живьём открыл календарь Серебряковой (со-преподавателя, добавленной
в tsk-440 отдельным слотом) — 0 участников: ученики были только у оператора,
tsk-440 создал ПАРАЛЛЕЛЬНЫЕ отдельные слоты вместо совместного ведения.
Прямой запрос: ученики должны быть видны СРАЗУ всем преподавателям одного
занятия, явка общая ("не отметился ни у кого" = пропуск). Архитектура,
подтверждённая `AskUserQuestion`: **ОДНО `lesson_occurrence` на несколько
преподавателей**, а не отдельное occurrence на каждого — тогда общая явка
получается БЕЗ отдельной синхронизации (один физический список участников).

Новые M2M-таблицы: `lesson_slot_teacher` (`slot_id`+`teacher_id`,
`is_active` — мягкое удаление) и `lesson_occurrence_teacher`
(`occurrence_id`+`teacher_id`, заполняется генератором из
`lesson_slot_teacher` на каждый тик — тем же паттерном, что участники).
`lesson_slot.teacher_id`/`lesson_occurrence.teacher_id` НЕ убраны — остаются
"основным/создателем" для обратной совместимости; все проверки владения
(`has_overlap`, `list_for_teacher`, `get_occurrence_for_teacher`) матчат
**колонку ИЛИ M2M** (не эксклюзивно M2M) — так уже существующие
одиночные слоты/occurrence (и старые тестовые фикстуры, создающие строки
напрямую через ORM) продолжают работать без миграции данных на своей
стороне; `create_lesson_slot` дополнительно СРАЗУ пишет строку в
`lesson_slot_teacher` для основного преподавателя.

`lesson_calendar_service.add_slot_teacher`/`remove_slot_teacher` — тот же
паттерн, что `add_slot_participant`: бэкфиллит уже сгенерированные будущие
occurrence слота, со-преподаватель не ждёт следующего тика генератора.
Cron `no-show` (`lesson_attendance_cron_service._mark_no_show`) уведомляет
ВСЕХ преподавателей occurrence через `lesson_occurrence_teacher`, не только
основного.

Скрипт `scripts/tsk443_convert_duplicate_slots_to_coteachers.py` перевёл
конкретный кейс (Серебрякова/Коротких/Ладесов, tsk-440) с отдельных слотов
на со-преподавание существующих слотов оператора; 8 дублирующих слотов
(id 14-21) деактивированы, их occurrence (0 участников у каждого) удалены.

**Часо-осведомлённый выбор преподавателя (tsk-443, продолжение — реальный
баг).** Ученик (Денис Ильин) привязан сразу к 4 преподавателям
(`student_teacher_links` — глобальная связь), форма «Записаться на занятие»
(SPW `BookLessonSection`) просила выбрать из всех 4, хотя на запрошенный
час (Пн 17:00) слот был только у одного. `GET /me/teachers?at=<ISO>` (+
`duration_minutes`, default 60) сужает список через
`lesson_calendar_service.list_teachers_for_time` — по слоту, НЕ по
преподавателю: возвращает по ОДНОМУ представителю (`slot.teacher_id`) НА
КАЖДЫЙ отдельный активный слот, покрывающий это время. Один слот с
несколькими со-преподавателями (совместное ведение) даёт ОДНОГО
представителя — выбор между ними бессмыслен, это одно и то же занятие.
Выбор нужен, только если время покрывают ДВА РАЗНЫХ независимых слота
(гарантированно не пересекаются по преподавателям — `has_overlap`).
Результат пересекается со списком ПРИВЯЗАННЫХ преподавателей ученика
(нельзя предложить записаться к постороннему); пустое пересечение или
отсутствие совпадающего слота — откат на полный список (обычный ad-hoc
вне расписания, поведение как раньше).

## Слияние учётных записей (tsk-442, применено)

**Проблема.** "Плавающие" ученики (заведены вручную по расписанию/календарю,
только `full_name`, без email/tg_id — см. tsk-435) при самостоятельной
регистрации могут завести ВТОРОЙ аккаунт: сопоставления по ФИО нет НИГДЕ
(`get_or_create_user_by_tg`/`_by_email`/`_by_vk` матчат строго по
`identity_link`, при несовпадении просто создают нового user). Люди вводят
ФИО по-разному: меняют местами имя/фамилию, опечатываются, не дописывают
фамилию. Отчество из сравнения исключено полностью.

**`users.is_active`** (default `true`) + **`users.merged_into_user_id`**
(self-FK, nullable) — деактивация вместо удаления, история (task_results,
attendance_event и т.д.) остаётся читаемой. `UsersRepository.
search_by_full_name_with_role` (используется пикерами "Добавить ученика"/
"Назначить курс") фильтрует `is_active=true` — слитые учётки из пикеров
пропадают сами, как только их данные перенесены (roster-эндпоинты вроде
`GET /teacher/students` дополнительного фильтра не требуют: связи
`student_teacher_links` переносятся при слиянии, у source их просто не
остаётся).

**`app/services/users_dedup_service.py`** — нечёткое сравнение ФИО:
`normalize_name_tokens` (нижний регистр, сортировка токенов → порядок слов
не важен, отброс отчества по типовым суффиксам при 3+ токенах) +
`fuzzy_name_match_score` (stdlib `difflib.SequenceMatcher` на склеенных
нормализованных токенах — устойчив к опечаткам и неполному вводу фамилии).
Сторонних библиотек нечёткого сравнения (rapidfuzz и т.п.) в проекте нет.

**Решение оператора (`AskUserQuestion`):** это НЕ auto-link и НЕ
"это вы?"-диалог в UI — только список кандидатов на дубль для ручного
разбора оператором/методистом:
- `scripts/tsk442_find_duplicate_candidates.py` — read-only, безопасно
  запускать в любой момент, печатает пары похожих ФИО среди `is_active=true`
  пользователей + помечает, есть ли у каждой стороны хоть одна
  `identity_link` ("плавающий" аккаунт vs уже входивший).
- `scripts/merge_users.py --source-id --target-id [--apply]` — write,
  протокол `/db-check` (dry-run → apply в одной транзакции →
  независимая верификация). Переносит все FK-ссылки на `users.id` из
  source в target: простые (свой `id` PK — прямой UPDATE) и
  конфликтующие (составной PK/UNIQUE — сперва DELETE строк source,
  дублирующих то, что уже есть у target по тому же второму ключу, затем
  UPDATE остальных). `user_session` НЕ переносится — удаляется (форсированный
  логаут деактивируемой учётки, а не тихая подмена личности активной
  сессии). Полный список таблиц — в самом скрипте (`SIMPLE_MOVES`/
  `CONFLICT_MOVES`/`DELETE_ON_MERGE`).

**Автослияние (продолжение, по итогам первого прогона на проде).** Оператор
попросил автоматически сливать пары с высокой уверенностью (порог 0.85-0.9,
default 0.9), остальное — по-прежнему на ручной разбор.
`users_dedup_service.select_auto_merge_pairs` — ОБЯЗАТЕЛЬНАЯ защита, не
опция: авто разрешено только когда (1) `score >= порога`, (2) РОВНО у одной
стороны есть `identity_link` (иначе непонятно, кто из двух "настоящий" —
без этой защиты первый же прогон авто-слил бы два реальных аккаунта
оператора, id=142 "Комлев Виктор" + id=2 "Виктор Комлев", score=1.00, оба
уже входили под своей identity), (3) пара единственная в обе стороны (нет
неоднозначности "с кем из нескольких сливать"). `scripts/
tsk442_auto_merge_duplicates.py` — dry-run по умолчанию, `--apply`
переиспользует `merge_users._run` по каждой auto-паре независимо (одна
упавшая не блокирует остальные), ручной список печатается тем же форматом.

## Read-контракты

OpenAPI-спека: [docs/openapi.json](../openapi.json) (снимок). Live-спека — на `/docs` и `/redoc` при запущенном сервере.

Подробные контракты — в `docs/API_*.md` (маршрут через [README.md](../../README.md)).

## Как безопасно смотреть данные

- MCP PostgreSQL (алиас `postgresql`) в read-only — схема, data diagnostics
- Write-запросы к БД — только при явном требовании задачи, с review-gate
- Любые изменения схемы — только через Alembic миграцию
