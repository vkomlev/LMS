"""AFTER DELETE пересчёт order_number под курсом — защита от re-entrancy (tsk-1542).

Revision ID: tsk1542_reorder_after_delete
Revises: tsk1538_exam_group_score
"""
from typing import Sequence, Union

from alembic import op


revision: str = "tsk1542_reorder_after_delete"
down_revision: Union[str, None] = "tsk1538_exam_group_score"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # tsk-1542: прежняя версия функции делала множественный UPDATE соседей
    # БЕЗ флага app.skip_course_parent_order_trigger. Если логический порядок
    # детей не совпадал с физическим (после одиночного PATCH order), вложенный
    # BEFORE UPDATE триггер пересчёта задевал ещё не обработанные кортежи
    # текущей команды → TriggeredDataChangeViolationError («tuple to be
    # updated was already modified by an operation triggered by the current
    # command») и 500 при штатном unlink (найдено при tsk-1540).
    #
    # Фикс по образцу самой же функции set_course_parent_order_number: весь
    # пересчёт соседей идёт под SET LOCAL флагом, который гасит повторный
    # вход в BEFORE-триггер. Заодно WHEN-условие на самом триггере — при
    # поднятом флаге вызов вообще не доходит до функции.
    op.execute("""
        CREATE OR REPLACE FUNCTION reorder_course_parents_after_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Если у удаленной записи был order_number, сдвигаем остальные влево
            IF OLD.order_number IS NOT NULL THEN
                PERFORM set_config('app.skip_course_parent_order_trigger', 'true', true);
                UPDATE course_parents
                SET order_number = order_number - 1
                WHERE parent_course_id = OLD.parent_course_id
                  AND order_number > OLD.order_number;
                PERFORM set_config('app.skip_course_parent_order_trigger', 'false', true);
            END IF;

            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_reorder_course_parents_after_delete ON course_parents;")
    op.execute("""
        CREATE TRIGGER trg_reorder_course_parents_after_delete
            AFTER DELETE ON course_parents
            FOR EACH ROW
            WHEN (current_setting('app.skip_course_parent_order_trigger', true) IS DISTINCT FROM 'true')
            EXECUTE FUNCTION reorder_course_parents_after_delete();
    """)


def downgrade() -> None:
    # Возвращаем поведение миграции 20260124_190000 (без защиты от re-entrancy).
    op.execute("DROP TRIGGER IF EXISTS trg_reorder_course_parents_after_delete ON course_parents;")
    op.execute("""
        CREATE TRIGGER trg_reorder_course_parents_after_delete
            AFTER DELETE ON course_parents
            FOR EACH ROW
            EXECUTE FUNCTION reorder_course_parents_after_delete();
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION reorder_course_parents_after_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.order_number IS NOT NULL THEN
                UPDATE course_parents
                SET order_number = order_number - 1
                WHERE parent_course_id = OLD.parent_course_id
                  AND order_number > OLD.order_number;
            END IF;

            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;
    """)
