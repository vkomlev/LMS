"""add courses triggers

Revision ID: add_courses_triggers
Revises: 
Create Date: 2025-01-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_courses_triggers'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Создание триггеров и ограничений для курсов:
    1. Автоматическая нумерация и пересчет order_number в user_courses
    2. Валидация циклов в иерархии курсов
    3. Ограничение для предотвращения самоссылок в зависимостях
    4. Триггер для пересчета после удаления курса
    5. Индекс для оптимизации запросов
    """
    
    # 1. Функция для автоматической нумерации order_number и пересчета при изменении
    op.execute("""
        CREATE OR REPLACE FUNCTION set_user_course_order_number()
        RETURNS TRIGGER AS $$
        DECLARE
            max_order INTEGER;
            old_order INTEGER;
        BEGIN
            -- Получаем максимальный порядковый номер для пользователя
            SELECT COALESCE(MAX(order_number), 0)
            INTO max_order
            FROM user_courses
            WHERE user_id = NEW.user_id
              AND (TG_OP = 'INSERT' OR course_id != NEW.course_id);
            
            -- При INSERT: если order_number не указан, ставим следующий номер
            IF TG_OP = 'INSERT' THEN
                IF NEW.order_number IS NULL THEN
                    NEW.order_number := max_order + 1;
                ELSE
                    -- Если указан явный order_number, сдвигаем существующие курсы
                    -- Используем временную таблицу для избежания рекурсии триггера
                    PERFORM set_config('app.skip_order_trigger', 'true', true);
                    UPDATE user_courses
                    SET order_number = order_number + 1
                    WHERE user_id = NEW.user_id
                      AND order_number >= NEW.order_number
                      AND course_id != NEW.course_id;
                    PERFORM set_config('app.skip_order_trigger', 'false', true);
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
                    -- Сдвигаем существующие курсы вправо
                    PERFORM set_config('app.skip_order_trigger', 'true', true);
                    UPDATE user_courses
                    SET order_number = order_number + 1
                    WHERE user_id = NEW.user_id
                      AND order_number >= NEW.order_number
                      AND course_id != NEW.course_id;
                    PERFORM set_config('app.skip_order_trigger', 'false', true);
                    END IF;
                    RETURN NEW;
                END IF;
                
                -- Если новый order_number NULL, ставим следующий номер
                IF NEW.order_number IS NULL THEN
                    NEW.order_number := max_order + 1;
                    -- Сдвигаем курсы, которые были после старого номера, влево
                    PERFORM set_config('app.skip_order_trigger', 'true', true);
                    UPDATE user_courses
                    SET order_number = order_number - 1
                    WHERE user_id = NEW.user_id
                      AND order_number > old_order
                      AND course_id != NEW.course_id;
                    PERFORM set_config('app.skip_order_trigger', 'false', true);
                    RETURN NEW;
                END IF;
                
                -- Если новый номер больше старого - сдвигаем курсы влево (уменьшаем номера)
                IF NEW.order_number > old_order THEN
                    -- Используем временную переменную для избежания рекурсии триггера
                    PERFORM set_config('app.skip_order_trigger', 'true', true);
                    UPDATE user_courses
                    SET order_number = order_number - 1
                    WHERE user_id = NEW.user_id
                      AND order_number > old_order
                      AND order_number <= NEW.order_number
                      AND course_id != NEW.course_id;
                    PERFORM set_config('app.skip_order_trigger', 'false', true);
                ELSE
                    -- Если новый номер меньше старого - сдвигаем курсы вправо (увеличиваем номера)
                    PERFORM set_config('app.skip_order_trigger', 'true', true);
                    UPDATE user_courses
                    SET order_number = order_number + 1
                    WHERE user_id = NEW.user_id
                      AND order_number >= NEW.order_number
                      AND order_number < old_order
                      AND course_id != NEW.course_id;
                    PERFORM set_config('app.skip_order_trigger', 'false', true);
                END IF;
            END IF;
            
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    # Создание триггера для автоматической нумерации
    # Добавляем проверку на флаг пропуска для избежания рекурсии
    op.execute("""
        CREATE TRIGGER trg_set_user_course_order_number
            BEFORE INSERT OR UPDATE ON user_courses
            FOR EACH ROW
            WHEN (current_setting('app.skip_order_trigger', true) IS DISTINCT FROM 'true')
            EXECUTE FUNCTION set_user_course_order_number();
    """)
    
    # 2. Функция для валидации циклов в иерархии курсов
    op.execute("""
        CREATE OR REPLACE FUNCTION check_course_hierarchy_cycle()
        RETURNS TRIGGER AS $$
        DECLARE
            parent_id INTEGER;
        BEGIN
            -- Проверка самоссылки
            IF NEW.parent_course_id = NEW.id THEN
                RAISE EXCEPTION 'Course cannot be its own parent';
            END IF;
            
            -- Проверка циклов через рекурсивный запрос
            IF NEW.parent_course_id IS NOT NULL THEN
                WITH RECURSIVE course_path AS (
                    SELECT id, parent_course_id, 1 as depth
                    FROM courses
                    WHERE id = NEW.parent_course_id
                    UNION ALL
                    SELECT c.id, c.parent_course_id, cp.depth + 1
                    FROM courses c
                    INNER JOIN course_path cp ON c.id = cp.parent_course_id
                    WHERE cp.depth < 100  -- защита от бесконечной рекурсии
                )
                SELECT id INTO parent_id
                FROM course_path
                WHERE id = NEW.id;
                
                IF parent_id IS NOT NULL THEN
                    RAISE EXCEPTION 'Circular reference detected: course % cannot be a descendant of course %', NEW.id, NEW.parent_course_id;
                END IF;
            END IF;
            
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    # Создание триггера для валидации иерархии
    op.execute("""
        CREATE TRIGGER trg_check_course_hierarchy_cycle
            BEFORE INSERT OR UPDATE ON courses
            FOR EACH ROW
            WHEN (NEW.parent_course_id IS NOT NULL)
            EXECUTE FUNCTION check_course_hierarchy_cycle();
    """)
    
    # 3. Ограничение для предотвращения самоссылок в зависимостях
    op.execute("""
        ALTER TABLE course_dependencies
        ADD CONSTRAINT check_no_self_dependency
            CHECK (course_id != required_course_id);
    """)
    
    # 4. Функция для пересчета после удаления курса
    op.execute("""
        CREATE OR REPLACE FUNCTION reorder_after_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Пересчитываем порядковые номера после удаления курса
            -- Только если у удаляемого курса был установлен order_number
            IF OLD.order_number IS NOT NULL THEN
                UPDATE user_courses
                SET order_number = order_number - 1
                WHERE user_id = OLD.user_id
                  AND order_number > OLD.order_number;
            END IF;
            
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    # Создание триггера для пересчета после удаления
    op.execute("""
        CREATE TRIGGER trg_reorder_after_delete
            AFTER DELETE ON user_courses
            FOR EACH ROW
            EXECUTE FUNCTION reorder_after_delete();
    """)
    
    # 5. Индекс для оптимизации запросов по order_number
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_courses_user_order 
            ON user_courses(user_id, order_number NULLS LAST);
    """)


def downgrade() -> None:
    """
    Откат изменений: удаление триггеров, функций и ограничений
    """
    
    # Удаление триггеров
    op.execute("DROP TRIGGER IF EXISTS trg_reorder_after_delete ON user_courses;")
    op.execute("DROP TRIGGER IF EXISTS trg_check_course_hierarchy_cycle ON courses;")
    op.execute("DROP TRIGGER IF EXISTS trg_set_user_course_order_number ON user_courses;")
    
    # Удаление функций
    op.execute("DROP FUNCTION IF EXISTS reorder_after_delete();")
    op.execute("DROP FUNCTION IF EXISTS check_course_hierarchy_cycle();")
    op.execute("DROP FUNCTION IF EXISTS set_user_course_order_number();")
    
    # Удаление ограничения
    op.execute("ALTER TABLE course_dependencies DROP CONSTRAINT IF EXISTS check_no_self_dependency;")
    
    # Удаление индекса
    op.execute("DROP INDEX IF EXISTS idx_user_courses_user_order;")
