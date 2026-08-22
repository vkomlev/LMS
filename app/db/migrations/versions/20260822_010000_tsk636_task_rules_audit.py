"""tsk-636: журнал изменений эталона задания (solution_rules) в task_audit.

Контекст. Калибровка tsk-590 нашла 10 работ с авто-вердиктом «не зачёт», которые
по НЫНЕШНИМ правилам заданий были бы зачётами, и не смогла отличить две причины:
сбой сравнения или правку эталона уже после сдачи. Разбор tsk-636 показал, что
это правка эталона — все десять, — но доказывать пришлось косвенными уликами:
«тот же ответ система зачла через неделю», «зачтён ответ, который нынешний эталон
отвергает». Прямого следа нет: `task_audit` (tsk-114) пишет только `course_id` и
`is_active`, а `content_provenance` заполняет один лишь веб-редактор (из десяти
случаев — один). Тот же пробел в третий раз: он же назван в шапке
`scripts/audit_stale_false_verdicts_tsk602.py` («истории изменения правил в базе
нет»).

Решение. Расширить существующий журнал вместо заведения второго: у `task_audit`
появляются `old_answer_key`/`new_answer_key`, а триггер `trg_task_audit_update`
начинает срабатывать ещё и на изменение `solution_rules`.

Почему пишем ВЫЖИМКУ, а не всё правило. Полный `solution_rules` содержит
`turtle_sim.expected_trace` — эталонную трассу рисунка в тысячи чисел (tsk-412);
две её копии на каждое изменение раздули бы журнал на порядок и ничего не
добавили бы к вопросу «почему вердикт стал другим». Функция `task_answer_key`
оставляет ровно то, от чего зависит вердикт: эталон короткого ответа вместе с
шагами нормализации, варианты выбора, режим начисления, флаги ручной/гибридной
проверки и обязательного вложения. Трасса черепахи заменяется на остальные поля
`turtle_sim` — факт правки виден, объём не растёт.

Цена на записи. Условие `WHEN` дополняется дешёвым сравнением jsonb
(`OLD.solution_rules IS DISTINCT FROM NEW.solution_rules`) — выжимка считается
только внутри функции, то есть лишь когда правило действительно изменилось.
Повторный импорт с теми же правилами строк не пишет: jsonb сравнивается по
значению, а не по тексту.

Revision ID: tsk636_task_rules_audit
Revises: tsk630_not_started_lessons
Create Date: 2026-08-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "tsk636_task_rules_audit"
down_revision: Union[str, None] = "tsk630_not_started_lessons"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Выжимка правила проверки: всё, от чего зависит вердикт, и ничего сверх того.
# `jsonb_strip_nulls` убирает незаполненные блоки, чтобы «поля не было» и
# «поле = null» не выглядели в журнале как разные состояния.
_ANSWER_KEY_FN = """
CREATE OR REPLACE FUNCTION task_answer_key(rules jsonb) RETURNS jsonb AS $$
    SELECT CASE
        WHEN rules IS NULL OR jsonb_typeof(rules) <> 'object' THEN NULL
        ELSE jsonb_strip_nulls(jsonb_build_object(
            'max_score',              rules -> 'max_score',
            'scoring_mode',           rules -> 'scoring_mode',
            'auto_check',             rules -> 'auto_check',
            'manual_review_required', rules -> 'manual_review_required',
            'partial_auto_check',     rules -> 'partial_auto_check',
            'requires_attachment',    rules -> 'requires_attachment',
            'correct_options',        rules -> 'correct_options',
            'partial_rules',          rules -> 'partial_rules',
            'short_answer',           rules -> 'short_answer',
            'table',                  rules -> 'table',
            'turtle_sim',             CASE
                                          WHEN jsonb_typeof(rules -> 'turtle_sim') = 'object'
                                          THEN (rules -> 'turtle_sim') - 'expected_trace'
                                      END
        ))
    END
$$ LANGUAGE sql IMMUTABLE;
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

# Прежнее тело функции (tsk-114) — для downgrade.
_LOG_FN_OLD = """
CREATE OR REPLACE FUNCTION log_task_audit() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        INSERT INTO task_audit (
            task_id, external_uid, action,
            old_course_id, new_course_id,
            old_is_active, new_is_active,
            changed_by, db_role
        ) VALUES (
            OLD.id, OLD.external_uid, 'DELETE',
            OLD.course_id, NULL,
            OLD.is_active, NULL,
            NULLIF(current_setting('app.audit_actor', true), ''),
            current_user
        );
        RETURN OLD;
    ELSE
        INSERT INTO task_audit (
            task_id, external_uid, action,
            old_course_id, new_course_id,
            old_is_active, new_is_active,
            changed_by, db_role
        ) VALUES (
            NEW.id, NEW.external_uid, 'UPDATE',
            OLD.course_id, NEW.course_id,
            OLD.is_active, NEW.is_active,
            NULLIF(current_setting('app.audit_actor', true), ''),
            current_user
        );
        RETURN NEW;
    END IF;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER_NEW = """
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

_TRIGGER_OLD = """
CREATE TRIGGER trg_task_audit_update
    AFTER UPDATE ON tasks
    FOR EACH ROW
    WHEN (
        current_setting('app.skip_task_audit_trigger', true) IS DISTINCT FROM 'true'
        AND (
            OLD.course_id IS DISTINCT FROM NEW.course_id
            OR OLD.is_active IS DISTINCT FROM NEW.is_active
        )
    )
    EXECUTE FUNCTION log_task_audit();
"""


def upgrade() -> None:
    """
    Шаги:
    1. Колонки `old_answer_key`/`new_answer_key` в `task_audit` (nullable —
       у записей tsk-114 их нет и не будет).
    2. Функция `task_answer_key(jsonb)` — выжимка правила проверки.
    3. Новое тело `log_task_audit()`: заполняет обе колонки.
    4. Пересоздание `trg_task_audit_update`: `WHEN` дополнен изменением
       `solution_rules` (условие триггера нельзя изменить на месте).
    """
    op.add_column(
        "task_audit",
        sa.Column(
            "old_answer_key",
            postgresql.JSONB,
            nullable=True,
            comment="Выжимка solution_rules ДО изменения (task_answer_key). "
                    "NULL — правило не менялось этим UPDATE либо запись сделана "
                    "до tsk-636.",
        ),
    )
    op.add_column(
        "task_audit",
        sa.Column(
            "new_answer_key",
            postgresql.JSONB,
            nullable=True,
            comment="Выжимка solution_rules ПОСЛЕ изменения (task_answer_key). "
                    "NULL — правило не менялось, запись до tsk-636 либо DELETE.",
        ),
    )

    op.execute(_ANSWER_KEY_FN)
    op.execute(_LOG_FN_NEW)
    op.execute("DROP TRIGGER IF EXISTS trg_task_audit_update ON tasks;")
    op.execute(_TRIGGER_NEW)


def downgrade() -> None:
    """Откат к поведению tsk-114: журнал снова пишет только курс и активность."""
    op.execute("DROP TRIGGER IF EXISTS trg_task_audit_update ON tasks;")
    op.execute(_TRIGGER_OLD)
    op.execute(_LOG_FN_OLD)
    op.execute("DROP FUNCTION IF EXISTS task_answer_key(jsonb);")
    op.drop_column("task_audit", "new_answer_key")
    op.drop_column("task_audit", "old_answer_key")
