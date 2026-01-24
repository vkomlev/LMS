"""Migrate course parents from one-to-many to many-to-many

Revision ID: 20260124_175541
Revises: 20250101_000000
Create Date: 2026-01-24 17:55:41.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260124_175541'
down_revision: Union[str, None] = 'add_courses_triggers'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Миграция: переход от одного родителя к множественным родителям.
    
    Шаги:
    1. Создать таблицу course_parents (многие-ко-многим)
    2. Перенести данные из parent_course_id в новую таблицу
    3. Удалить триггер и функцию для старой структуры
    4. Создать новую функцию и триггер для проверки циклов с множественными родителями
    5. Удалить колонку parent_course_id и связанные ограничения
    """
    
    # 1. Создание таблицы course_parents
    op.create_table(
        'course_parents',
        sa.Column('course_id', sa.Integer(), nullable=False, comment='ID дочернего курса'),
        sa.Column('parent_course_id', sa.Integer(), nullable=False, comment='ID родительского курса'),
        sa.ForeignKeyConstraint(['course_id'], ['courses.id'], ondelete='CASCADE', name='course_parents_course_id_fkey'),
        sa.ForeignKeyConstraint(['parent_course_id'], ['courses.id'], ondelete='CASCADE', name='course_parents_parent_course_id_fkey'),
        sa.PrimaryKeyConstraint('course_id', 'parent_course_id', name='course_parents_pkey'),
        comment='Иерархия курсов: связь многие-ко-многим (курс может иметь несколько родителей)'
    )
    
    # Создание индекса для оптимизации запросов
    op.create_index('idx_course_parents_course', 'course_parents', ['course_id'])
    op.create_index('idx_course_parents_parent', 'course_parents', ['parent_course_id'])
    
    # 2. Перенос данных из parent_course_id в новую таблицу
    op.execute("""
        INSERT INTO course_parents (course_id, parent_course_id)
        SELECT id, parent_course_id
        FROM courses
        WHERE parent_course_id IS NOT NULL
    """)
    
    # 3. Удаление старого триггера и функции
    op.execute("DROP TRIGGER IF EXISTS trg_check_course_hierarchy_cycle ON courses")
    op.execute("DROP FUNCTION IF EXISTS check_course_hierarchy_cycle()")
    
    # 4. Создание новой функции для проверки циклов с множественными родителями
    op.execute("""
        CREATE OR REPLACE FUNCTION check_course_hierarchy_cycle()
        RETURNS TRIGGER AS $$
        DECLARE
            parent_id INTEGER;
            descendant_id INTEGER;
        BEGIN
            -- Проверка самоссылки
            IF NEW.course_id = NEW.parent_course_id THEN
                RAISE EXCEPTION 'Course cannot be its own parent';
            END IF;
            
            -- Проверка циклов через рекурсивный запрос
            -- Проверяем, не является ли новый родитель потомком текущего курса
            WITH RECURSIVE course_descendants AS (
                -- Базовый случай: прямые дети (курсы, у которых текущий курс является родителем)
                SELECT cp.course_id as id, 1 as depth
                FROM course_parents cp
                WHERE cp.parent_course_id = NEW.course_id
                
                UNION ALL
                
                -- Рекурсивный случай: дети детей
                SELECT cp.course_id, cd.depth + 1
                FROM course_parents cp
                INNER JOIN course_descendants cd ON cp.parent_course_id = cd.id
                WHERE cd.depth < 100  -- защита от бесконечной рекурсии
            )
            SELECT id INTO descendant_id
            FROM course_descendants
            WHERE id = NEW.parent_course_id;
            
            IF descendant_id IS NOT NULL THEN
                RAISE EXCEPTION 'Circular reference detected: course % cannot be a descendant of course %', 
                    NEW.parent_course_id, NEW.course_id;
            END IF;
            
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    # Создание триггера для новой таблицы
    op.execute("""
        CREATE TRIGGER trg_check_course_hierarchy_cycle
            BEFORE INSERT OR UPDATE ON course_parents
            FOR EACH ROW
            EXECUTE FUNCTION check_course_hierarchy_cycle();
    """)
    
    # 5. Добавление CHECK CONSTRAINT для предотвращения самоссылок
    op.execute("""
        ALTER TABLE course_parents
        ADD CONSTRAINT check_no_self_parent
            CHECK (course_id != parent_course_id);
    """)
    
    # 6. Удаление старого индекса и внешнего ключа для parent_course_id
    op.drop_index('idx_courses_parent', table_name='courses')
    op.drop_constraint('courses_parent_course_id_fkey', 'courses', type_='foreignkey')
    
    # 7. Удаление колонки parent_course_id
    op.drop_column('courses', 'parent_course_id')


def downgrade() -> None:
    """
    Откат миграции: возврат к одному родителю.
    
    Шаги:
    1. Добавить колонку parent_course_id обратно
    2. Перенести данные из course_parents (берем первого родителя, если их несколько)
    3. Удалить триггер и функцию для новой структуры
    4. Создать старую функцию и триггер
    5. Удалить таблицу course_parents
    """
    
    # 1. Добавление колонки parent_course_id обратно
    op.add_column('courses', sa.Column('parent_course_id', sa.Integer(), nullable=True))
    
    # 2. Восстановление внешнего ключа и индекса
    op.create_foreign_key(
        'courses_parent_course_id_fkey',
        'courses', 'courses',
        ['parent_course_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_index('idx_courses_parent', 'courses', ['parent_course_id'])
    
    # 3. Перенос данных из course_parents (берем первого родителя для каждого курса)
    op.execute("""
        UPDATE courses c
        SET parent_course_id = (
            SELECT parent_course_id
            FROM course_parents cp
            WHERE cp.course_id = c.id
            ORDER BY parent_course_id
            LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1 FROM course_parents cp WHERE cp.course_id = c.id
        )
    """)
    
    # 4. Удаление нового триггера и функции
    op.execute("DROP TRIGGER IF EXISTS trg_check_course_hierarchy_cycle ON course_parents")
    op.execute("DROP FUNCTION IF EXISTS check_course_hierarchy_cycle()")
    
    # 5. Создание старой функции и триггера
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
    
    op.execute("""
        CREATE TRIGGER trg_check_course_hierarchy_cycle
            BEFORE INSERT OR UPDATE ON courses
            FOR EACH ROW
            WHEN (NEW.parent_course_id IS NOT NULL)
            EXECUTE FUNCTION check_course_hierarchy_cycle();
    """)
    
    # 6. Удаление таблицы course_parents
    op.drop_table('course_parents')
