"""tsk-760: у задания появляется след «когда его последний раз трогали».

Зачем. ContentBackbone переиздаёт курсы (ADR-0055 CB): правки, внесённые в
источник, должны доезжать до учеников, но при этом нельзя стереть то, что
человек поправил уже здесь, в LMS. Отличить одно от другого сегодня нечем:
`content_provenance` заполняет только веб-редактор (на 01.09 — 3 задания из
7749), а `task_audit` (tsk-114 + tsk-636) пишет смену курса, активности и
эталона, но не касается условия. У `tasks` нет `updated_at` — это уже третий
случай, когда его отсутствие мешает: tsk-113 (кто перенёс 353 задания),
tsk-636 (когда правили эталон), теперь tsk-760.

Что делает миграция.

1. **`tasks.updated_at`** — отметка последней правки СОДЕРЖИМОГО. Ставится
   BEFORE-триггером и только когда реально изменилось то, что перезаписывает
   импорт: `task_content`, `solution_rules`, `difficulty_id`, `max_score`,
   `course_id`, `is_active`. Перестановка `order_position` (её массово двигают
   триггеры порядка, tsk-345) правкой НЕ считается — иначе переиздание считало
   бы тронутыми задания, которых никто не касался.

   Колонка добавляется БЕЗ `server_default` и НЕ заполняется для существующих
   строк — урок tsk-692: `ADD COLUMN ... DEFAULT <expr>` заполняет умолчанием
   всю таблицу, и 7749 заданий разом получили бы дату накатки, то есть
   выглядели бы «только что правленными». `NULL` читается честно: «с тех пор,
   как мы начали это записывать, задание не трогали». Историю правок до этой
   миграции колонка не восстанавливает и не пытается — для неё есть разовая
   простановка `content_provenance` по задачам, где правки делались руками.

2. **`task_audit` расширяется на условие задания.** У журнала появляются
   `old_content_key`/`new_content_key` — отпечаток `task_content` (sha256 от
   нормализованного jsonb), а триггер `trg_task_audit_update` начинает
   срабатывать ещё и на изменение `task_content`. Пишем отпечаток, а не сам
   текст: условие бывает в десятки килобайт (PDF-импорт, таблицы), две копии на
   каждое изменение раздули бы журнал, а на вопрос «правили ли условие и когда»
   отпечатка достаточно. Восстановление содержимого — не задача журнала: у
   переиздания для этого свой снимок «до».

Цена на записи. `WHEN` дополняется сравнением jsonb по значению
(`IS DISTINCT FROM`) — отпечаток считается только внутри функции, то есть лишь
когда условие действительно изменилось. Повторный импорт с тем же содержимым
строк в журнал не пишет и `updated_at` не двигает.

Rollback: `alembic downgrade tsk741_homework` — снимает колонку, оба триггера
и функции возвращаются к редакции tsk-636.

Revision ID: tsk760_task_touch_trail
Revises: tsk741_homework
Create Date: 2026-09-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "tsk760_task_touch_trail"
down_revision: Union[str, None] = "tsk741_homework"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Отпечаток условия задания. `jsonb_strip_nulls` — чтобы «поля нет» и
# «поле = null» не выглядели разными состояниями (тот же приём, что в
# task_answer_key, tsk-636).
_CONTENT_KEY_FN = """
CREATE OR REPLACE FUNCTION task_content_key(content jsonb) RETURNS text AS $$
    SELECT CASE
        WHEN content IS NULL OR jsonb_typeof(content) <> 'object' THEN NULL
        ELSE encode(
            sha256(convert_to(jsonb_strip_nulls(content)::text, 'UTF8')),
            'hex'
        )
    END
$$ LANGUAGE sql IMMUTABLE;
"""

# Отметка последней правки содержимого. Условие «что считать правкой» живёт в
# WHEN триггера, а не здесь: функция должна оставаться тривиальной.
_TOUCH_FN = """
CREATE OR REPLACE FUNCTION set_task_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_TOUCH_TRIGGER = """
CREATE TRIGGER trg_task_set_updated_at
    BEFORE UPDATE ON tasks
    FOR EACH ROW
    WHEN (
        OLD.task_content IS DISTINCT FROM NEW.task_content
        OR OLD.solution_rules IS DISTINCT FROM NEW.solution_rules
        OR OLD.difficulty_id IS DISTINCT FROM NEW.difficulty_id
        OR OLD.max_score IS DISTINCT FROM NEW.max_score
        OR OLD.course_id IS DISTINCT FROM NEW.course_id
        OR OLD.is_active IS DISTINCT FROM NEW.is_active
    )
    EXECUTE FUNCTION set_task_updated_at();
"""

_LOG_FN_NEW = """
CREATE OR REPLACE FUNCTION log_task_audit() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        INSERT INTO task_audit (
            task_id, external_uid, action,
            old_course_id, new_course_id,
            old_is_active, new_is_active,
            old_answer_key, new_answer_key,
            old_content_key, new_content_key,
            changed_by, db_role
        ) VALUES (
            OLD.id, OLD.external_uid, 'DELETE',
            OLD.course_id, NULL,
            OLD.is_active, NULL,
            task_answer_key(OLD.solution_rules), NULL,
            task_content_key(OLD.task_content), NULL,
            NULLIF(current_setting('app.audit_actor', true), ''),
            current_user
        );
        RETURN OLD;
    ELSE
        INSERT INTO task_audit (
            task_id, external_uid, action,
            old_course_id, new_course_id,
            old_is_active, new_is_active,
            old_answer_key, new_answer_key,
            old_content_key, new_content_key,
            changed_by, db_role
        ) VALUES (
            NEW.id, NEW.external_uid, 'UPDATE',
            OLD.course_id, NEW.course_id,
            OLD.is_active, NEW.is_active,
            CASE WHEN OLD.solution_rules IS DISTINCT FROM NEW.solution_rules
                 THEN task_answer_key(OLD.solution_rules) END,
            CASE WHEN OLD.solution_rules IS DISTINCT FROM NEW.solution_rules
                 THEN task_answer_key(NEW.solution_rules) END,
            CASE WHEN OLD.task_content IS DISTINCT FROM NEW.task_content
                 THEN task_content_key(OLD.task_content) END,
            CASE WHEN OLD.task_content IS DISTINCT FROM NEW.task_content
                 THEN task_content_key(NEW.task_content) END,
            NULLIF(current_setting('app.audit_actor', true), ''),
            current_user
        );
        RETURN NEW;
    END IF;
END;
$$ LANGUAGE plpgsql;
"""

# Прежнее тело функции (tsk-636) — для downgrade.
_LOG_FN_OLD = """
CREATE OR REPLACE FUNCTION log_task_audit() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        INSERT INTO task_audit (
            task_id, external_uid, action,
            old_course_id, new_course_id,
            old_is_active, new_is_active,
            old_answer_key, new_answer_key,
            changed_by, db_role
        ) VALUES (
            OLD.id, OLD.external_uid, 'DELETE',
            OLD.course_id, NULL,
            OLD.is_active, NULL,
            task_answer_key(OLD.solution_rules), NULL,
            NULLIF(current_setting('app.audit_actor', true), ''),
            current_user
        );
        RETURN OLD;
    ELSE
        INSERT INTO task_audit (
            task_id, external_uid, action,
            old_course_id, new_course_id,
            old_is_active, new_is_active,
            old_answer_key, new_answer_key,
            changed_by, db_role
        ) VALUES (
            NEW.id, NEW.external_uid, 'UPDATE',
            OLD.course_id, NEW.course_id,
            OLD.is_active, NEW.is_active,
            CASE WHEN OLD.solution_rules IS DISTINCT FROM NEW.solution_rules
                 THEN task_answer_key(OLD.solution_rules) END,
            CASE WHEN OLD.solution_rules IS DISTINCT FROM NEW.solution_rules
                 THEN task_answer_key(NEW.solution_rules) END,
            NULLIF(current_setting('app.audit_actor', true), ''),
            current_user
        );
        RETURN NEW;
    END IF;
END;
$$ LANGUAGE plpgsql;
"""

_AUDIT_TRIGGER_NEW = """
CREATE TRIGGER trg_task_audit_update
    AFTER UPDATE ON tasks
    FOR EACH ROW
    WHEN (
        current_setting('app.skip_task_audit_trigger', true) IS DISTINCT FROM 'true'
        AND (
            OLD.course_id IS DISTINCT FROM NEW.course_id
            OR OLD.is_active IS DISTINCT FROM NEW.is_active
            OR OLD.solution_rules IS DISTINCT FROM NEW.solution_rules
            OR OLD.task_content IS DISTINCT FROM NEW.task_content
        )
    )
    EXECUTE FUNCTION log_task_audit();
"""

_AUDIT_TRIGGER_OLD = """
CREATE TRIGGER trg_task_audit_update
    AFTER UPDATE ON tasks
    FOR EACH ROW
    WHEN (
        current_setting('app.skip_task_audit_trigger', true) IS DISTINCT FROM 'true'
        AND (
            OLD.course_id IS DISTINCT FROM NEW.course_id
            OR OLD.is_active IS DISTINCT FROM NEW.is_active
            OR OLD.solution_rules IS DISTINCT FROM NEW.solution_rules
        )
    )
    EXECUTE FUNCTION log_task_audit();
"""


def upgrade() -> None:
    """1. updated_at + триггер; 2. отпечаток условия в журнале."""
    # 1. Отметка последней правки содержимого.
    op.add_column(
        "tasks",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "Когда последний раз меняли содержимое задания (условие, "
                "правило проверки, сложность, балл, курс, активность). "
                "Ставится триггером trg_task_set_updated_at. NULL — с момента "
                "введения отметки (tsk-760) задание не трогали; про более "
                "раннее колонка ничего не утверждает."
            ),
        ),
    )
    op.execute(_TOUCH_FN)
    op.execute(_TOUCH_TRIGGER)

    # 2. Отпечаток условия в журнале изменений.
    op.add_column(
        "task_audit",
        sa.Column(
            "old_content_key", sa.Text, nullable=True,
            comment="sha256 условия задания ДО изменения (task_content_key)",
        ),
    )
    op.add_column(
        "task_audit",
        sa.Column(
            "new_content_key", sa.Text, nullable=True,
            comment="sha256 условия задания ПОСЛЕ изменения",
        ),
    )
    op.execute(_CONTENT_KEY_FN)
    op.execute(_LOG_FN_NEW)
    op.execute("DROP TRIGGER IF EXISTS trg_task_audit_update ON tasks;")
    op.execute(_AUDIT_TRIGGER_NEW)


def downgrade() -> None:
    """Возврат к редакции tsk-636: журнал без отпечатка условия, без updated_at."""
    op.execute("DROP TRIGGER IF EXISTS trg_task_audit_update ON tasks;")
    op.execute(_AUDIT_TRIGGER_OLD)
    op.execute(_LOG_FN_OLD)
    op.execute("DROP FUNCTION IF EXISTS task_content_key(jsonb);")
    op.drop_column("task_audit", "new_content_key")
    op.drop_column("task_audit", "old_content_key")

    op.execute("DROP TRIGGER IF EXISTS trg_task_set_updated_at ON tasks;")
    op.execute("DROP FUNCTION IF EXISTS set_task_updated_at();")
    op.drop_column("tasks", "updated_at")
