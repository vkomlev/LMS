"""Tasks: order_position column, backfill, triggers, index (зеркало materials)

Revision ID: tasks_order_position_triggers
Revises: m12_y6_optimistic_pass
Create Date: 2026-05-21 12:00:00.000000

Контекст:
- Добавляет в `tasks` поле `order_position INTEGER NULL` с триггерами PL/pgSQL,
  зеркало `materials.order_position` (см. docs/database-triggers-contract.md разделы 7-8).
- Бекфилл существующих 567 строк через `ROW_NUMBER() OVER (PARTITION BY course_id ORDER BY id ASC)`
  — порядок задач для активных студентов в Learning Engine не меняется.
- AFTER DELETE триггер сразу statement-level + REFERENCING OLD TABLE
  (учитываем урок materials → fix-миграция 20260205_140000).
- Индекс `idx_tasks_course_order` для get_by_course и LE next-item picker.

⚠️ ОТЛИЧИЕ ОТ MATERIALS: `tasks.external_uid` имеет глобальный UNIQUE
(не UNIQUE(course_id, external_uid) как у materials). На триггер order_position
это не влияет (партиция по course_id), но при bulk-импорте следует учитывать.

Authority:
- docs/briefs/tsk-004-tasks-order-position.md
- docs/specs/2026-05-21-tz-tasks-order-position-stage1.md
- docs/database-triggers-contract.md разделы 13-14
"""
from typing import Sequence, Union

from alembic import op


revision: str = "tasks_order_position_triggers"
down_revision: Union[str, None] = "m12_y6_optimistic_pass"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Шаги:
    1. ADD COLUMN order_position INTEGER NULL.
    2. Бекфилл существующих строк по ROW_NUMBER() OVER (PARTITION BY course_id ORDER BY id ASC).
    3. Индекс idx_tasks_course_order.
    4. PL/pgSQL функция set_task_order_position() + триггер BEFORE INSERT/UPDATE FOR EACH ROW.
    5. PL/pgSQL функция reorder_tasks_after_delete() + триггер AFTER DELETE FOR EACH STATEMENT.
    """

    # 1. Колонка (asyncpg не допускает multi-statement — две отдельные команды)
    op.execute("ALTER TABLE tasks ADD COLUMN order_position INTEGER NULL")
    op.execute(
        "COMMENT ON COLUMN tasks.order_position IS "
        "'Позиция в курсе (NULL = автоматически в конец)'"
    )

    # 2. Бекфилл по id ASC внутри course_id — сохраняем существующий порядок LE
    op.execute(
        """
        UPDATE tasks t
        SET order_position = rn.new_pos
        FROM (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY course_id
                       ORDER BY id ASC
                   )::integer AS new_pos
            FROM tasks
        ) rn
        WHERE t.id = rn.id;
        """
    )

    # 3. Индекс по (course_id, order_position NULLS LAST)
    op.execute(
        """
        CREATE INDEX idx_tasks_course_order
        ON tasks (course_id, order_position NULLS LAST);
        """
    )

    # 4. Функция автонумерации order_position (копия set_material_order_position
    #    из миграции materials_structure_triggers с заменой имён).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_task_order_position()
        RETURNS TRIGGER AS $$
        DECLARE
            max_order INTEGER;
            old_order INTEGER;
        BEGIN
            SELECT COALESCE(MAX(order_position), 0)
            INTO max_order
            FROM tasks
            WHERE course_id = NEW.course_id
              AND (TG_OP = 'INSERT' OR id != NEW.id);

            IF TG_OP = 'INSERT' THEN
                IF NEW.order_position IS NULL THEN
                    NEW.order_position := max_order + 1;
                ELSE
                    PERFORM set_config('app.skip_task_order_trigger', 'true', true);
                    UPDATE tasks
                    SET order_position = order_position + 1
                    WHERE course_id = NEW.course_id
                      AND order_position >= NEW.order_position
                      AND id != NEW.id;
                    PERFORM set_config('app.skip_task_order_trigger', 'false', true);
                END IF;
            END IF;

            IF TG_OP = 'UPDATE' THEN
                old_order := OLD.order_position;

                IF (NEW.order_position IS NULL AND old_order IS NULL)
                   OR NEW.order_position = old_order THEN
                    RETURN NEW;
                END IF;

                IF old_order IS NULL THEN
                    IF NEW.order_position IS NULL THEN
                        NEW.order_position := max_order + 1;
                    ELSE
                        PERFORM set_config('app.skip_task_order_trigger', 'true', true);
                        UPDATE tasks
                        SET order_position = order_position + 1
                        WHERE course_id = NEW.course_id
                          AND order_position >= NEW.order_position
                          AND id != NEW.id;
                        PERFORM set_config('app.skip_task_order_trigger', 'false', true);
                    END IF;
                    RETURN NEW;
                END IF;

                IF NEW.order_position IS NULL THEN
                    NEW.order_position := max_order + 1;
                    PERFORM set_config('app.skip_task_order_trigger', 'true', true);
                    UPDATE tasks
                    SET order_position = order_position - 1
                    WHERE course_id = NEW.course_id
                      AND order_position > old_order
                      AND id != NEW.id;
                    PERFORM set_config('app.skip_task_order_trigger', 'false', true);
                    RETURN NEW;
                END IF;

                IF NEW.order_position > old_order THEN
                    PERFORM set_config('app.skip_task_order_trigger', 'true', true);
                    UPDATE tasks
                    SET order_position = order_position - 1
                    WHERE course_id = NEW.course_id
                      AND order_position > old_order
                      AND order_position <= NEW.order_position
                      AND id != NEW.id;
                    PERFORM set_config('app.skip_task_order_trigger', 'false', true);
                ELSE
                    PERFORM set_config('app.skip_task_order_trigger', 'true', true);
                    UPDATE tasks
                    SET order_position = order_position + 1
                    WHERE course_id = NEW.course_id
                      AND order_position >= NEW.order_position
                      AND order_position < old_order
                      AND id != NEW.id;
                    PERFORM set_config('app.skip_task_order_trigger', 'false', true);
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_set_task_order_position
            BEFORE INSERT OR UPDATE ON tasks
            FOR EACH ROW
            WHEN (current_setting('app.skip_task_order_trigger', true) IS DISTINCT FROM 'true')
            EXECUTE FUNCTION set_task_order_position();
        """
    )

    # 5. Функция пересчёта после удаления — сразу statement-level с transition table
    #    (учитываем урок materials: row-level вариант ломается TriggeredDataChangeViolationError).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reorder_tasks_after_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            PERFORM set_config('app.skip_task_order_trigger', 'true', true);
            UPDATE tasks t
            SET order_position = rn.new_pos
            FROM (
                SELECT id, course_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY course_id
                           ORDER BY order_position NULLS LAST, id
                       )::integer AS new_pos
                FROM tasks
                WHERE course_id IN (SELECT DISTINCT course_id FROM old_rows)
            ) rn
            WHERE t.id = rn.id
              AND t.course_id = rn.course_id
              AND (t.order_position IS DISTINCT FROM rn.new_pos);
            PERFORM set_config('app.skip_task_order_trigger', 'false', true);
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_reorder_tasks_after_delete
            AFTER DELETE ON tasks
            REFERENCING OLD TABLE AS old_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION reorder_tasks_after_delete();
        """
    )


def downgrade() -> None:
    """
    Откат: удаление триггеров, функций, индекса и колонки.
    Данные order_position теряются — повторный upgrade восстановит по id ASC.
    """
    op.execute("DROP TRIGGER IF EXISTS trg_reorder_tasks_after_delete ON tasks;")
    op.execute("DROP FUNCTION IF EXISTS reorder_tasks_after_delete();")
    op.execute("DROP TRIGGER IF EXISTS trg_set_task_order_position ON tasks;")
    op.execute("DROP FUNCTION IF EXISTS set_task_order_position();")
    op.execute("DROP INDEX IF EXISTS idx_tasks_course_order;")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS order_position;")
