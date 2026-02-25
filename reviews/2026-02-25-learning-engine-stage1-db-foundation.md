# Learning Engine V1, Этап 1 (DB foundation)

**Дата:** 2026-02-25

## Контекст

Реализация этапа 1 по ТЗ: подготовка схемы БД и инфраструктуры без изменения текущего поведения API.

## Сделано

1. **Миграция** `app/db/migrations/versions/20260225_100000_learning_engine_stage1_db_foundation.py`
   - user_courses: колонка `is_active boolean not null default true`
   - Таблицы: student_material_progress, student_task_limit_override, student_course_state, learning_events (с FK, PK, индексами)
   - tasks: max_attempts, time_limit_sec (check time_limit_sec > 0)
   - attempts: time_expired boolean not null default false
   - downgrade: полный откат

2. **Feature flag** `LEARNING_ENGINE_V1` в `app/core/config.py` (по умолчанию false).

3. **Backfill** `scripts/backfill_student_course_state.py` — идемпотентное заполнение student_course_state (NOT_STARTED / IN_PROGRESS / COMPLETED) по user_courses и завершённым попыткам.

4. **ORM** — в моделях user_courses, attempts, tasks добавлены новые колонки для совместимости с БД.

5. **Документация**
   - docs/learning-engine-stage1-schema.md — описание изменений схемы
   - docs/learning-engine-stage1-smoke.md — smoke-инструкция (up/down, backfill, регрессии)

6. **Smoke**
   - Миграции up/down/up проверены
   - Backfill выполняется без ошибок, повторно — идемпотентно
   - Текущие API (GET attempts и др.) работают

## Артефакты

- Миграция: up/down
- Скрипт backfill
- Документация схемы и smoke
- Полный diff: [reviews/2026-02-25-learning-engine-stage1-db-foundation.diff](2026-02-25-learning-engine-stage1-db-foundation.diff)
