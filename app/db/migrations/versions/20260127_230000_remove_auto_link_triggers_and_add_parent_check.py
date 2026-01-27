"""remove auto link triggers and add parent check

Revision ID: remove_auto_link_triggers
Revises: add_teacher_courses
Create Date: 2026-01-27 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'remove_auto_link_triggers'
down_revision: Union[str, None] = 'add_teacher_courses'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Упрощение логики работы с триггерами:
    1. Удаление триггеров автоматической привязки/отвязки детей
    2. Удаление триггера синхронизации при добавлении ребенка
    3. Добавление проверки, что курс не имеет родителей перед привязкой препода/студента
    """
    
    # 1. Удаляем триггеры автоматической привязки/отвязки детей
    op.execute("DROP TRIGGER IF EXISTS trg_auto_unlink_teacher_course_children ON teacher_courses;")
    op.execute("DROP TRIGGER IF EXISTS trg_auto_link_teacher_course_children ON teacher_courses;")
    
    # 2. Удаляем триггер синхронизации при добавлении ребенка
    op.execute("DROP TRIGGER IF EXISTS trg_sync_teacher_courses_on_child_added ON course_parents;")
    
    # 3. Удаляем функции (если они больше не используются)
    op.execute("DROP FUNCTION IF EXISTS auto_unlink_teacher_course_children();")
    op.execute("DROP FUNCTION IF EXISTS auto_link_teacher_course_children();")
    op.execute("DROP FUNCTION IF EXISTS sync_teacher_courses_on_child_added();")
    op.execute("DROP FUNCTION IF EXISTS sync_teacher_courses_on_child_removed();")
    
    # 4. Создаем функцию для проверки, что курс не имеет родителей
    op.execute("""
        CREATE OR REPLACE FUNCTION check_course_has_no_parents()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Проверяем, есть ли у курса родители
            IF EXISTS (
                SELECT 1 
                FROM course_parents 
                WHERE course_id = NEW.course_id
            ) THEN
                RAISE EXCEPTION 'Course % has parents. Teachers and students can only be linked to courses without parents.', NEW.course_id;
            END IF;
            
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    # 5. Создаем триггер для проверки перед привязкой преподавателя
    op.execute("""
        CREATE TRIGGER trg_check_teacher_course_no_parents
            BEFORE INSERT ON teacher_courses
            FOR EACH ROW
            EXECUTE FUNCTION check_course_has_no_parents();
    """)
    
    # 6. Создаем функцию для проверки студента (аналогично)
    op.execute("""
        CREATE OR REPLACE FUNCTION check_user_course_has_no_parents()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Проверяем, есть ли у курса родители
            IF EXISTS (
                SELECT 1 
                FROM course_parents 
                WHERE course_id = NEW.course_id
            ) THEN
                RAISE EXCEPTION 'Course % has parents. Teachers and students can only be linked to courses without parents.', NEW.course_id;
            END IF;
            
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    # 7. Создаем триггер для проверки перед привязкой студента
    op.execute("""
        CREATE TRIGGER trg_check_user_course_no_parents
            BEFORE INSERT ON user_courses
            FOR EACH ROW
            EXECUTE FUNCTION check_user_course_has_no_parents();
    """)


def downgrade() -> None:
    """
    Откат изменений: восстановление триггеров авто-привязки/отвязки
    """
    
    # Удаляем новые триггеры проверки
    op.execute("DROP TRIGGER IF EXISTS trg_check_user_course_no_parents ON user_courses;")
    op.execute("DROP TRIGGER IF EXISTS trg_check_teacher_course_no_parents ON teacher_courses;")
    
    # Удаляем функции проверки
    op.execute("DROP FUNCTION IF EXISTS check_user_course_has_no_parents();")
    op.execute("DROP FUNCTION IF EXISTS check_course_has_no_parents();")
    
    # Восстанавливаем триггеры (нужно будет восстановить функции из предыдущей миграции)
    # Это сложно сделать автоматически, поэтому оставляем комментарий
    # op.execute("CREATE TRIGGER ...")  # Восстановление требует ручного вмешательства
