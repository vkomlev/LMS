"""Add order_number to course_parents with triggers

Revision ID: 20260124_190000
Revises: 20260124_175541
Create Date: 2026-01-24 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260124_190000'
down_revision: Union[str, None] = '20260124_175541'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Добавление order_number в course_parents и триггеров для управления порядком.
    
    Логика:
    1. Добавляем колонку order_number в таблицу course_parents
    2. Создаем функцию для автоматической нумерации и пересчета order_number
    3. Создаем триггер для автоматической нумерации при INSERT/UPDATE
    4. Создаем функцию для пересчета после DELETE
    5. Создаем триггер для пересчета после DELETE
    """
    
    # 1. Добавляем колонку order_number
    op.add_column(
        'course_parents',
        sa.Column(
            'order_number',
            sa.SmallInteger(),
            nullable=True,
            comment='Порядковый номер подкурса внутри родительского курса. Автоматически устанавливается триггером.'
        )
    )
    
    # 2. Функция для автоматической нумерации order_number и пересчета при изменении
    op.execute("""
        CREATE OR REPLACE FUNCTION set_course_parent_order_number()
        RETURNS TRIGGER AS $$
        DECLARE
            max_order INTEGER;
            old_order INTEGER;
        BEGIN
            -- Получаем максимальный порядковый номер для родительского курса
            SELECT COALESCE(MAX(order_number), 0)
            INTO max_order
            FROM course_parents
            WHERE parent_course_id = NEW.parent_course_id
              AND (TG_OP = 'INSERT' OR course_id != NEW.course_id);
            
            -- При INSERT: если order_number не указан, ставим следующий номер
            IF TG_OP = 'INSERT' THEN
                IF NEW.order_number IS NULL THEN
                    NEW.order_number := max_order + 1;
                ELSE
                    -- Если указан явный order_number, сдвигаем существующие подкурсы
                    PERFORM set_config('app.skip_course_parent_order_trigger', 'true', true);
                    UPDATE course_parents
                    SET order_number = order_number + 1
                    WHERE parent_course_id = NEW.parent_course_id
                      AND order_number >= NEW.order_number
                      AND course_id != NEW.course_id;
                    PERFORM set_config('app.skip_course_parent_order_trigger', 'false', true);
                END IF;
            END IF;
            
            -- При UPDATE: если order_number изменился, пересчитываем остальные
            IF TG_OP = 'UPDATE' THEN
                old_order := OLD.order_number;
                
                -- Если order_number не изменился или оба NULL, ничего не делаем
                IF (NEW.order_number IS NULL AND old_order IS NULL) OR NEW.order_number = old_order THEN
                    RETURN NEW;
                END IF;
                
                -- Если старый order_number был NULL, обрабатываем как INSERT
                IF old_order IS NULL THEN
                    IF NEW.order_number IS NULL THEN
                        NEW.order_number := max_order + 1;
                    ELSE
                        -- Сдвигаем существующие подкурсы вправо
                        PERFORM set_config('app.skip_course_parent_order_trigger', 'true', true);
                        UPDATE course_parents
                        SET order_number = order_number + 1
                        WHERE parent_course_id = NEW.parent_course_id
                          AND order_number >= NEW.order_number
                          AND course_id != NEW.course_id;
                        PERFORM set_config('app.skip_course_parent_order_trigger', 'false', true);
                    END IF;
                    RETURN NEW;
                END IF;
                
                -- Если новый order_number NULL, ставим следующий номер
                IF NEW.order_number IS NULL THEN
                    NEW.order_number := max_order + 1;
                    -- Сдвигаем подкурсы, которые были после старого номера, влево
                    PERFORM set_config('app.skip_course_parent_order_trigger', 'true', true);
                    UPDATE course_parents
                    SET order_number = order_number - 1
                    WHERE parent_course_id = NEW.parent_course_id
                      AND order_number > old_order
                      AND course_id != NEW.course_id;
                    PERFORM set_config('app.skip_course_parent_order_trigger', 'false', true);
                    RETURN NEW;
                END IF;
                
                -- Если новый номер больше старого - сдвигаем подкурсы влево (уменьшаем номера)
                IF NEW.order_number > old_order THEN
                    PERFORM set_config('app.skip_course_parent_order_trigger', 'true', true);
                    UPDATE course_parents
                    SET order_number = order_number - 1
                    WHERE parent_course_id = NEW.parent_course_id
                      AND order_number > old_order
                      AND order_number <= NEW.order_number
                      AND course_id != NEW.course_id;
                    PERFORM set_config('app.skip_course_parent_order_trigger', 'false', true);
                ELSE
                    -- Если новый номер меньше старого - сдвигаем подкурсы вправо (увеличиваем номера)
                    PERFORM set_config('app.skip_course_parent_order_trigger', 'true', true);
                    UPDATE course_parents
                    SET order_number = order_number + 1
                    WHERE parent_course_id = NEW.parent_course_id
                      AND order_number >= NEW.order_number
                      AND order_number < old_order
                      AND course_id != NEW.course_id;
                    PERFORM set_config('app.skip_course_parent_order_trigger', 'false', true);
                END IF;
            END IF;
            
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    # 3. Создание триггера для автоматической нумерации
    op.execute("""
        CREATE TRIGGER trg_set_course_parent_order_number
            BEFORE INSERT OR UPDATE ON course_parents
            FOR EACH ROW
            WHEN (current_setting('app.skip_course_parent_order_trigger', true) IS DISTINCT FROM 'true')
            EXECUTE FUNCTION set_course_parent_order_number();
    """)
    
    # 4. Функция для пересчета order_number после удаления
    op.execute("""
        CREATE OR REPLACE FUNCTION reorder_course_parents_after_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Если у удаленной записи был order_number, сдвигаем остальные влево
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
    
    # 5. Создание триггера для пересчета после удаления
    op.execute("""
        CREATE TRIGGER trg_reorder_course_parents_after_delete
            AFTER DELETE ON course_parents
            FOR EACH ROW
            EXECUTE FUNCTION reorder_course_parents_after_delete();
    """)


def downgrade() -> None:
    """Откат изменений: удаление триггеров и колонки order_number."""
    
    # Удаляем триггеры
    op.execute("DROP TRIGGER IF EXISTS trg_reorder_course_parents_after_delete ON course_parents;")
    op.execute("DROP TRIGGER IF EXISTS trg_set_course_parent_order_number ON course_parents;")
    
    # Удаляем функции
    op.execute("DROP FUNCTION IF EXISTS reorder_course_parents_after_delete();")
    op.execute("DROP FUNCTION IF EXISTS set_course_parent_order_number();")
    
    # Удаляем колонку
    op.drop_column('course_parents', 'order_number')
