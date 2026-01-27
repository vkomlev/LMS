"""add teacher courses table and triggers

Revision ID: add_teacher_courses
Revises: 20260124_190000_add_order_number_to_course_parents
Create Date: 2026-01-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_teacher_courses'
down_revision: Union[str, None] = '20260124_190000'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Создание таблицы teacher_courses и триггеров для автоматической синхронизации иерархии курсов:
    1. Таблица teacher_courses (many-to-many связь преподавателей и курсов)
    2. Индексы для оптимизации производительности
    3. Триггер автоматической привязки детей при привязке родителя
    4. Триггер автоматической отвязки детей при отвязке родителя
    5. Триггер синхронизации при добавлении ребенка в иерархию
    6. Триггер синхронизации при удалении ребенка из иерархии
    """
    
    # 1. Создание таблицы teacher_courses
    op.create_table(
        'teacher_courses',
        sa.Column('teacher_id', sa.Integer(), nullable=False, comment='ID преподавателя'),
        sa.Column('course_id', sa.Integer(), nullable=False, comment='ID курса'),
        sa.Column('linked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='Дата и время привязки'),
        sa.ForeignKeyConstraint(['teacher_id'], ['users.id'], ondelete='CASCADE', name='teacher_courses_teacher_id_fkey'),
        sa.ForeignKeyConstraint(['course_id'], ['courses.id'], ondelete='CASCADE', name='teacher_courses_course_id_fkey'),
        sa.PrimaryKeyConstraint('teacher_id', 'course_id', name='teacher_courses_pkey'),
        comment='Привязка преподавателей к курсам. Автоматическая привязка детей при привязке родителя и синхронизация при изменении иерархии реализованы в БД через триггеры.'
    )
    
    # 2. Создание индексов для оптимизации
    op.create_index('idx_teacher_courses_teacher_id', 'teacher_courses', ['teacher_id'], unique=False)
    op.create_index('idx_teacher_courses_course_id', 'teacher_courses', ['course_id'], unique=False)
    op.create_index('idx_teacher_courses_linked_at', 'teacher_courses', ['linked_at'], unique=False)
    op.create_index('idx_teacher_courses_teacher_linked_at', 'teacher_courses', ['teacher_id', 'linked_at'], unique=False)
    op.create_index('idx_teacher_courses_course_linked_at', 'teacher_courses', ['course_id', 'linked_at'], unique=False)
    
    # 3. Функция автоматической привязки детей при привязке родителя
    op.execute("""
        CREATE OR REPLACE FUNCTION auto_link_teacher_course_children()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Проверяем, не нужно ли пропустить триггер (для предотвращения рекурсии)
            IF current_setting('app.skip_auto_link_trigger', true) = 'true' THEN
                RETURN NEW;
            END IF;
            
            -- Рекурсивно находим всех потомков курса (с ограничением глубины)
            WITH RECURSIVE course_descendants AS (
                -- Прямые дети
                SELECT cp.course_id, 1 as depth
                FROM course_parents cp
                WHERE cp.parent_course_id = NEW.course_id
                
                UNION ALL
                
                -- Дети детей (рекурсия)
                SELECT cp.course_id, cd.depth + 1
                FROM course_parents cp
                INNER JOIN course_descendants cd ON cp.parent_course_id = cd.course_id
                WHERE cd.depth < 20  -- Ограничение глубины рекурсии для оптимизации
            )
            -- Вставляем связи для всех потомков
            INSERT INTO teacher_courses (teacher_id, course_id, linked_at)
            SELECT NEW.teacher_id, cd.course_id, NEW.linked_at
            FROM course_descendants cd
            ON CONFLICT (teacher_id, course_id) DO NOTHING;
            
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    # Создание триггера для автоматической привязки детей
    op.execute("""
        CREATE TRIGGER trg_auto_link_teacher_course_children
            AFTER INSERT ON teacher_courses
            FOR EACH ROW
            WHEN (current_setting('app.skip_auto_link_trigger', true) IS DISTINCT FROM 'true')
            EXECUTE FUNCTION auto_link_teacher_course_children();
    """)
    
    # 4. Функция автоматической отвязки детей при отвязке родителя
    op.execute("""
        CREATE OR REPLACE FUNCTION auto_unlink_teacher_course_children()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Проверяем, не нужно ли пропустить триггер
            IF current_setting('app.skip_auto_unlink_trigger', true) = 'true' THEN
                RETURN OLD;
            END IF;
            
            -- Рекурсивно находим всех потомков курса (с ограничением глубины)
            WITH RECURSIVE course_descendants AS (
                SELECT cp.course_id, 1 as depth
                FROM course_parents cp
                WHERE cp.parent_course_id = OLD.course_id
                
                UNION ALL
                
                SELECT cp.course_id, cd.depth + 1
                FROM course_parents cp
                INNER JOIN course_descendants cd ON cp.parent_course_id = cd.course_id
                WHERE cd.depth < 20  -- Ограничение глубины рекурсии
            )
            -- Удаляем связи для всех потомков
            DELETE FROM teacher_courses
            WHERE teacher_id = OLD.teacher_id
              AND course_id IN (SELECT course_id FROM course_descendants);
            
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    # Создание триггера для автоматической отвязки детей
    op.execute("""
        CREATE TRIGGER trg_auto_unlink_teacher_course_children
            AFTER DELETE ON teacher_courses
            FOR EACH ROW
            WHEN (current_setting('app.skip_auto_unlink_trigger', true) IS DISTINCT FROM 'true')
            EXECUTE FUNCTION auto_unlink_teacher_course_children();
    """)
    
    # 5. Функция синхронизации при добавлении ребенка в иерархию
    op.execute("""
        CREATE OR REPLACE FUNCTION sync_teacher_courses_on_child_added()
        RETURNS TRIGGER AS $$
        DECLARE
            teacher_record RECORD;
        BEGIN
            -- Находим всех преподавателей, привязанных к родительскому курсу
            FOR teacher_record IN
                SELECT teacher_id
                FROM teacher_courses
                WHERE course_id = NEW.parent_course_id
            LOOP
                -- Привязываем преподавателя к новому ребенку
                PERFORM set_config('app.skip_auto_link_trigger', 'true', true);
                INSERT INTO teacher_courses (teacher_id, course_id, linked_at)
                VALUES (teacher_record.teacher_id, NEW.course_id, NOW())
                ON CONFLICT (teacher_id, course_id) DO NOTHING;
                
                -- Привязываем ко всем потомкам нового ребенка (рекурсивно)
                WITH RECURSIVE course_descendants AS (
                    SELECT cp.course_id, 1 as depth
                    FROM course_parents cp
                    WHERE cp.parent_course_id = NEW.course_id
                    
                    UNION ALL
                    
                    SELECT cp.course_id, cd.depth + 1
                    FROM course_parents cp
                    INNER JOIN course_descendants cd ON cp.parent_course_id = cd.course_id
                    WHERE cd.depth < 20  -- Ограничение глубины рекурсии
                )
                INSERT INTO teacher_courses (teacher_id, course_id, linked_at)
                SELECT teacher_record.teacher_id, cd.course_id, NOW()
                FROM course_descendants cd
                ON CONFLICT (teacher_id, course_id) DO NOTHING;
                
                PERFORM set_config('app.skip_auto_link_trigger', 'false', true);
            END LOOP;
            
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    # Создание триггера для синхронизации при добавлении ребенка
    op.execute("""
        CREATE TRIGGER trg_sync_teacher_courses_on_child_added
            AFTER INSERT ON course_parents
            FOR EACH ROW
            EXECUTE FUNCTION sync_teacher_courses_on_child_added();
    """)
    
    # 6. Функция синхронизации при удалении ребенка из иерархии
    # Используем подход с сохранением данных в переменные перед использованием teacher_courses
    op.execute("""
        CREATE OR REPLACE FUNCTION sync_teacher_courses_on_child_removed()
        RETURNS TRIGGER AS $$
        DECLARE
            teacher_ids_to_remove INTEGER[];
            descendant_course_ids INTEGER[];
            parent_teacher_ids INTEGER[];
            remaining_parent_ids INTEGER[];
            teachers_with_other_parents_arr INTEGER[];
        BEGIN
            -- Проверяем, не нужно ли пропустить триггер
            IF current_setting('app.skip_auto_unlink_trigger', true) = 'true' THEN
                RETURN OLD;
            END IF;
            
            -- Сохраняем ID преподавателей родительского курса в массив (ДО использования в подзапросе)
            SELECT ARRAY_AGG(DISTINCT teacher_id) INTO parent_teacher_ids
            FROM teacher_courses
            WHERE course_id = OLD.parent_course_id;
            
            -- Сохраняем ID других родителей курса (ДО использования teacher_courses)
            SELECT ARRAY_AGG(DISTINCT parent_course_id) INTO remaining_parent_ids
            FROM course_parents
            WHERE course_id = OLD.course_id
              AND parent_course_id != OLD.parent_course_id;
            
            -- Сохраняем ID преподавателей, привязанных к удаляемому курсу (ДО использования в подзапросе)
            SELECT ARRAY_AGG(DISTINCT teacher_id) INTO teacher_ids_to_remove
            FROM teacher_courses
            WHERE course_id = OLD.course_id;
            
            -- Если есть другие родители, проверяем для каждого преподавателя,
            -- привязан ли он хотя бы к одному из оставшихся родителей
            -- Если НЕ привязан ни к одному - удаляем связь с курсом
            -- Используем EXECUTE для обхода ограничения PostgreSQL
            IF remaining_parent_ids IS NOT NULL 
               AND array_length(remaining_parent_ids, 1) > 0 
               AND teacher_ids_to_remove IS NOT NULL 
               AND array_length(teacher_ids_to_remove, 1) > 0 THEN
                
                -- Сохраняем ID преподавателей, привязанных к оставшимся родителям (ДО удаления)
                -- Используем отдельный запрос для обхода ограничения
                EXECUTE format('
                    SELECT ARRAY_AGG(DISTINCT teacher_id)
                    FROM teacher_courses
                    WHERE course_id = ANY($1)
                      AND teacher_id = ANY($2)
                ') INTO teachers_with_other_parents_arr
                USING remaining_parent_ids, teacher_ids_to_remove;
                
                -- Исключаем преподавателей, которые привязаны хотя бы к одному другому родителю
                IF teachers_with_other_parents_arr IS NOT NULL AND array_length(teachers_with_other_parents_arr, 1) > 0 THEN
                    SELECT ARRAY_AGG(teacher_id) INTO teacher_ids_to_remove
                    FROM unnest(teacher_ids_to_remove) AS teacher_id
                    WHERE teacher_id != ALL(teachers_with_other_parents_arr);
                END IF;
            END IF;
            
            -- Удаляем связи для преподавателей, которые не привязаны к другим родителям
            -- (или если других родителей нет - удаляем всех)
            -- Используем EXECUTE для обхода ограничения PostgreSQL
            IF teacher_ids_to_remove IS NOT NULL AND array_length(teacher_ids_to_remove, 1) > 0 THEN
                EXECUTE format('
                    DELETE FROM teacher_courses
                    WHERE course_id = $1
                      AND teacher_id = ANY($2)
                ') USING OLD.course_id, teacher_ids_to_remove;
            END IF;
            
            -- Находим всех потомков удаляемого ребенка (с ограничением глубины)
            WITH RECURSIVE course_descendants AS (
                SELECT cp.course_id, 1 as depth
                FROM course_parents cp
                WHERE cp.parent_course_id = OLD.course_id
                
                UNION ALL
                
                SELECT cp.course_id, cd.depth + 1
                FROM course_parents cp
                INNER JOIN course_descendants cd ON cp.parent_course_id = cd.course_id
                WHERE cd.depth < 20  -- Ограничение глубины рекурсии
            )
            -- Сохраняем ID потомков в массив
            SELECT ARRAY_AGG(course_id) INTO descendant_course_ids
            FROM course_descendants;
            
            -- Удаляем связи для всех потомков (используем сохраненные данные)
            IF descendant_course_ids IS NOT NULL 
               AND array_length(descendant_course_ids, 1) > 0 
               AND parent_teacher_ids IS NOT NULL 
               AND array_length(parent_teacher_ids, 1) > 0 THEN
                DELETE FROM teacher_courses
                WHERE teacher_id = ANY(parent_teacher_ids)
                  AND course_id = ANY(descendant_course_ids);
            END IF;
            
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    # Создание триггера для синхронизации при удалении ребенка
    # ⚠️ ТЕХНИЧЕСКИЙ ДОЛГ: Триггер отключен из-за ограничения PostgreSQL
    # PostgreSQL не позволяет изменять таблицу teacher_courses в AFTER DELETE триггере на course_parents,
    # если teacher_courses используется в запросе триггера (TriggeredDataChangeViolationError).
    # Логика перенесена в TeacherCoursesRepository.sync_on_child_removed()
    # См. docs/database-triggers-contract.md
    # op.execute("""
    #     CREATE TRIGGER trg_sync_teacher_courses_on_child_removed
    #         AFTER DELETE ON course_parents
    #         FOR EACH ROW
    #         WHEN (current_setting('app.skip_auto_unlink_trigger', true) IS DISTINCT FROM 'true')
    #         EXECUTE FUNCTION sync_teacher_courses_on_child_removed();
    # """)


def downgrade() -> None:
    """
    Откат изменений: удаление триггеров, функций и таблицы
    """
    # Удаление триггеров
    op.execute("DROP TRIGGER IF EXISTS trg_sync_teacher_courses_on_child_removed ON course_parents;")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_teacher_courses_on_child_added ON course_parents;")
    op.execute("DROP TRIGGER IF EXISTS trg_auto_unlink_teacher_course_children ON teacher_courses;")
    op.execute("DROP TRIGGER IF EXISTS trg_auto_link_teacher_course_children ON teacher_courses;")
    
    # Удаление функций
    op.execute("DROP FUNCTION IF EXISTS sync_teacher_courses_on_child_removed();")
    op.execute("DROP FUNCTION IF EXISTS sync_teacher_courses_on_child_added();")
    op.execute("DROP FUNCTION IF EXISTS auto_unlink_teacher_course_children();")
    op.execute("DROP FUNCTION IF EXISTS auto_link_teacher_course_children();")
    
    # Удаление таблицы (индексы удалятся автоматически)
    op.drop_table('teacher_courses')
