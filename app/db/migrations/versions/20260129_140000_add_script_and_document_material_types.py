"""Add script and document material types

Revision ID: add_script_document_types
Revises: materials_structure_triggers
Create Date: 2026-01-29 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "add_script_document_types"
down_revision: Union[str, None] = "materials_structure_triggers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавить в enum content_type значения script и document."""
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON e.enumtypid = t.oid WHERE t.typname = 'content_type' AND e.enumlabel = 'script') THEN
                ALTER TYPE content_type ADD VALUE 'script';
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON e.enumtypid = t.oid WHERE t.typname = 'content_type' AND e.enumlabel = 'document') THEN
                ALTER TYPE content_type ADD VALUE 'document';
            END IF;
        END $$;
    """)


def downgrade() -> None:
    """PostgreSQL не поддерживает удаление значений enum простым способом.
    Для отката потребовалась бы пересоздание типа и колонки.
    """
    pass
