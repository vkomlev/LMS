# Правки по ревью: Learning Engine Stage 1

**Дата:** 2026-02-25

## Учтённые замечания

### [P1] Backfill учитывает только активные курсы

- **Было:** CTE `uc` брал все строки из `user_courses`.
- **Сделано:** В backfill в CTE `uc` добавлен фильтр `WHERE is_active IS TRUE`. Для деактивированных курсов запись в `student_course_state` не создаётся и не обновляется.
- **Файл:** `scripts/backfill_student_course_state.py`.

### [P1] CHECK-ограничения на допустимые значения

- **Было:** `student_material_progress.status` и `student_course_state.state` без CHECK; невалидные значения допускались.
- **Сделано:** Добавлена миграция `20260225_110000_learning_engine_stage1_check_constraints.py`:
  - `student_material_progress`: CHECK `status IN ('completed')`;
  - `student_course_state`: CHECK `state IN ('NOT_STARTED','IN_PROGRESS','COMPLETED','BLOCKED_DEPENDENCY')`;
  - `tasks`: CHECK `max_attempts IS NULL OR max_attempts > 0`.
- **Файл:** `app/db/migrations/versions/20260225_110000_learning_engine_stage1_check_constraints.py`.

### [P1] Ограничение на tasks.max_attempts > 0

- Реализовано в той же миграции CHECK-ограничений (см. выше).

### [P2] Backfill не зависит от полного Settings

- **Было:** Скрипт использовал `Settings()`, требовались `DATABASE_URL` и `VALID_API_KEYS`.
- **Сделано:** Скрипт читает только `os.environ.get("DATABASE_URL")`, импорт `Settings` удалён. Запуск возможен в окружении с одним `DATABASE_URL` (в т.ч. из .env).
- **Файл:** `scripts/backfill_student_course_state.py`.

## Обновлённая документация

- `docs/learning-engine-stage1-schema.md` — добавлены CHECK для status, state, max_attempts; уточнён backfill (is_active, DATABASE_URL).
- `docs/learning-engine-stage1-smoke.md` — требование только DATABASE_URL для backfill; разделы 8–9: проверка CHECK и поведения при is_active=false.

## Проверка

- Миграция `learning_engine_stage1_checks` применена (alembic upgrade head).
- В БД через MCP подтверждены ограничения: `student_material_progress_status_check`, `student_course_state_state_check`, `tasks_max_attempts_positive`.
- Backfill выполняется без Settings (DATABASE_URL из .env).

Полный diff: [reviews/2026-02-25-learning-engine-stage1-review-fixes.diff](2026-02-25-learning-engine-stage1-review-fixes.diff)
