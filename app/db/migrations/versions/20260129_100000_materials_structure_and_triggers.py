"""Materials table structure and order_position triggers

Revision ID: materials_structure_triggers
Revises: remove_auto_link_triggers
Create Date: 2026-01-29 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = 'materials_structure_triggers'
down_revision: Union[str, None] = 'remove_auto_link_triggers'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Этап 1: Обновление структуры БД для материалов.
    1. Добавить поля: title, description, caption, is_active, external_uid, created_at, updated_at
    2. Расширить enum content_type: audio, image, office_document
    3. Сделать order_position NULLABLE
    4. Добавить ограничение уникальности (course_id, external_uid) и индекс
    5. Триггеры: автоматическая нумерация order_position, пересчёт после DELETE, обновление updated_at
    """

    # 1. Новые колонки
    op.add_column(
        'materials',
        sa.Column(
            'title',
            sa.String(500),
            nullable=False,
            server_default=text("''"),
            comment='Заголовок материала',
        ),
    )
    op.add_column(
        'materials',
        sa.Column(
            'description',
            sa.Text(),
            nullable=True,
            comment='Описание/инструкции по использованию',
        ),
    )
    op.add_column(
        'materials',
        sa.Column(
            'caption',
            sa.Text(),
            nullable=True,
            comment='Подпись к материалу',
        ),
    )
    op.add_column(
        'materials',
        sa.Column(
            'is_active',
            sa.Boolean(),
            nullable=False,
            server_default=text('true'),
            comment='Активен ли материал',
        ),
    )
    op.add_column(
        'materials',
        sa.Column(
            'external_uid',
            sa.String(255),
            nullable=True,
            comment='Внешний идентификатор для импорта (уникален в паре с course_id)',
        ),
    )
    op.add_column(
        'materials',
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=text('now()'),
            comment='Дата создания',
        ),
    )
    op.add_column(
        'materials',
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=text('now()'),
            comment='Дата обновления',
        ),
    )

    # 2. Расширение enum content_type (идемпотентно)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON e.enumtypid = t.oid WHERE t.typname = 'content_type' AND e.enumlabel = 'audio') THEN
                ALTER TYPE content_type ADD VALUE 'audio';
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON e.enumtypid = t.oid WHERE t.typname = 'content_type' AND e.enumlabel = 'image') THEN
                ALTER TYPE content_type ADD VALUE 'image';
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON e.enumtypid = t.oid WHERE t.typname = 'content_type' AND e.enumlabel = 'office_document') THEN
                ALTER TYPE content_type ADD VALUE 'office_document';
            END IF;
        END $$;
    """)

    # 3. order_position — сделать nullable
    op.alter_column(
        'materials',
        'order_position',
        existing_type=sa.Integer(),
        nullable=True,
        comment='Позиция в курсе (NULL = автоматически в конец)',
    )

    # 4. Уникальность (course_id, external_uid) и индекс для порядка материалов курса
    op.create_unique_constraint(
        'uq_materials_course_external_uid',
        'materials',
        ['course_id', 'external_uid'],
    )
    op.execute("""
        CREATE INDEX idx_materials_course_order
        ON materials (course_id, order_position NULLS LAST);
    """)

    # 5. Функция автоматической нумерации order_position
    op.execute("""
        CREATE OR REPLACE FUNCTION set_material_order_position()
        RETURNS TRIGGER AS $$
        DECLARE
            max_order INTEGER;
            old_order INTEGER;
        BEGIN
            SELECT COALESCE(MAX(order_position), 0)
            INTO max_order
            FROM materials
            WHERE course_id = NEW.course_id
              AND (TG_OP = 'INSERT' OR id != NEW.id);

            IF TG_OP = 'INSERT' THEN
                IF NEW.order_position IS NULL THEN
                    NEW.order_position := max_order + 1;
                ELSE
                    PERFORM set_config('app.skip_material_order_trigger', 'true', true);
                    UPDATE materials
                    SET order_position = order_position + 1
                    WHERE course_id = NEW.course_id
                      AND order_position >= NEW.order_position
                      AND id != NEW.id;
                    PERFORM set_config('app.skip_material_order_trigger', 'false', true);
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
                        PERFORM set_config('app.skip_material_order_trigger', 'true', true);
                        UPDATE materials
                        SET order_position = order_position + 1
                        WHERE course_id = NEW.course_id
                          AND order_position >= NEW.order_position
                          AND id != NEW.id;
                        PERFORM set_config('app.skip_material_order_trigger', 'false', true);
                    END IF;
                    RETURN NEW;
                END IF;

                IF NEW.order_position IS NULL THEN
                    NEW.order_position := max_order + 1;
                    PERFORM set_config('app.skip_material_order_trigger', 'true', true);
                    UPDATE materials
                    SET order_position = order_position - 1
                    WHERE course_id = NEW.course_id
                      AND order_position > old_order
                      AND id != NEW.id;
                    PERFORM set_config('app.skip_material_order_trigger', 'false', true);
                    RETURN NEW;
                END IF;

                IF NEW.order_position > old_order THEN
                    PERFORM set_config('app.skip_material_order_trigger', 'true', true);
                    UPDATE materials
                    SET order_position = order_position - 1
                    WHERE course_id = NEW.course_id
                      AND order_position > old_order
                      AND order_position <= NEW.order_position
                      AND id != NEW.id;
                    PERFORM set_config('app.skip_material_order_trigger', 'false', true);
                ELSE
                    PERFORM set_config('app.skip_material_order_trigger', 'true', true);
                    UPDATE materials
                    SET order_position = order_position + 1
                    WHERE course_id = NEW.course_id
                      AND order_position >= NEW.order_position
                      AND order_position < old_order
                      AND id != NEW.id;
                    PERFORM set_config('app.skip_material_order_trigger', 'false', true);
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER trg_set_material_order_position
            BEFORE INSERT OR UPDATE ON materials
            FOR EACH ROW
            WHEN (current_setting('app.skip_material_order_trigger', true) IS DISTINCT FROM 'true')
            EXECUTE FUNCTION set_material_order_position();
    """)

    # Функция пересчёта после удаления
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

    # Триггер обновления updated_at
    op.execute("""
        CREATE OR REPLACE FUNCTION set_material_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at := NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER trg_material_updated_at
            BEFORE UPDATE ON materials
            FOR EACH ROW
            EXECUTE FUNCTION set_material_updated_at();
    """)


def downgrade() -> None:
    """Откат: удаление триггеров, ограничений, индексов и новых колонок."""

    op.execute("DROP TRIGGER IF EXISTS trg_material_updated_at ON materials;")
    op.execute("DROP FUNCTION IF EXISTS set_material_updated_at();")

    op.execute("DROP TRIGGER IF EXISTS trg_reorder_materials_after_delete ON materials;")
    op.execute("DROP FUNCTION IF EXISTS reorder_materials_after_delete();")

    op.execute("DROP TRIGGER IF EXISTS trg_set_material_order_position ON materials;")
    op.execute("DROP FUNCTION IF EXISTS set_material_order_position();")

    op.execute("DROP INDEX IF EXISTS idx_materials_course_order;")
    op.drop_constraint('uq_materials_course_external_uid', 'materials', type_='unique')

    op.alter_column(
        'materials',
        'order_position',
        existing_type=sa.Integer(),
        nullable=False,
        comment='Позиция в курсе',
    )

    op.drop_column('materials', 'updated_at')
    op.drop_column('materials', 'created_at')
    op.drop_column('materials', 'external_uid')
    op.drop_column('materials', 'is_active')
    op.drop_column('materials', 'caption')
    op.drop_column('materials', 'description')
    op.drop_column('materials', 'title')

    # Удаление значений enum в PostgreSQL требует пересоздания типа — не выполняем в downgrade
    # ALTER TYPE content_type REMOVE VALUE не поддерживается в старых версиях PG
    # При необходимости откат enum делается отдельной миграцией
