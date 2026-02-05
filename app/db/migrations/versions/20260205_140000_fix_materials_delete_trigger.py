"""Fix materials DELETE trigger: FOR EACH STATEMENT to avoid TriggeredDataChangeViolationError

Revision ID: fix_materials_delete_trigger
Revises: add_script_document_types
Create Date: 2026-02-05 14:00:00.000000

Ошибка: при DELETE материала срабатывал триггер AFTER DELETE FOR EACH ROW,
который делал UPDATE других строк. В PostgreSQL это вызывает
TriggeredDataChangeViolationError («кортеж уже модифицирован»).
Решение: перейти на FOR EACH STATEMENT с REFERENCING OLD TABLE —
триггер выполняется один раз после завершения DELETE, UPDATE идёт в чистом контексте.

"""
from typing import Sequence, Union

from alembic import op


revision: str = "fix_materials_delete_trigger"
down_revision: Union[str, None] = "add_script_document_types"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Удаляем старый триггер и функцию
    op.execute("DROP TRIGGER IF EXISTS trg_reorder_materials_after_delete ON materials;")
    op.execute("DROP FUNCTION IF EXISTS reorder_materials_after_delete();")

    # Новая функция: statement-level, использует transition table.
    # Для каждого затронутого course_id — полный пересчёт order_position (1,2,3,...).
    # skip_material_order_trigger отключает BEFORE UPDATE при нашем UPDATE.
    op.execute("""
        CREATE OR REPLACE FUNCTION reorder_materials_after_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            PERFORM set_config('app.skip_material_order_trigger', 'true', true);
            UPDATE materials m
            SET order_position = rn.new_pos
            FROM (
                SELECT id, course_id,
                       ROW_NUMBER() OVER (PARTITION BY course_id ORDER BY order_position NULLS LAST, id)::integer AS new_pos
                FROM materials
                WHERE course_id IN (SELECT DISTINCT course_id FROM old_rows)
            ) rn
            WHERE m.id = rn.id AND m.course_id = rn.course_id
              AND (m.order_position IS DISTINCT FROM rn.new_pos);
            PERFORM set_config('app.skip_material_order_trigger', 'false', true);
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # Триггер FOR EACH STATEMENT с REFERENCING OLD TABLE
    op.execute("""
        CREATE TRIGGER trg_reorder_materials_after_delete
            AFTER DELETE ON materials
            REFERENCING OLD TABLE AS old_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION reorder_materials_after_delete();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_reorder_materials_after_delete ON materials;")
    op.execute("DROP FUNCTION IF EXISTS reorder_materials_after_delete();")

    # Восстанавливаем старый триггер (row-level)
    op.execute("""
        CREATE OR REPLACE FUNCTION reorder_materials_after_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.order_position IS NOT NULL THEN
                UPDATE materials
                SET order_position = order_position - 1
                WHERE course_id = OLD.course_id
                  AND order_position > OLD.order_position;
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_reorder_materials_after_delete
            AFTER DELETE ON materials
            FOR EACH ROW
            EXECUTE FUNCTION reorder_materials_after_delete();
    """)
