"""Add uid column to difficulties for import mapping

Revision ID: add_difficulties_uid
Revises: fix_materials_delete_trigger
Create Date: 2026-02-16 10:00:00.000000

Колонка uid — уникальный идентификатор уровня сложности для маппинга при импорте
(по аналогии с course_uid у курсов). Значения заполняются из code.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = "add_difficulties_uid"
down_revision: Union[str, None] = "fix_materials_delete_trigger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "difficulties",
        sa.Column(
            "uid",
            sa.String(64),
            nullable=True,
            comment="Уникальный идентификатор для импорта (маппинг через БД)",
        ),
    )
    # Заполняем uid из code в нижнем регистре (theory, easy, normal, hard, project)
    op.execute(text("UPDATE difficulties SET uid = LOWER(code) WHERE uid IS NULL"))
    op.alter_column(
        "difficulties",
        "uid",
        nullable=False,
    )
    op.create_unique_constraint("difficulties_uid_key", "difficulties", ["uid"])


def downgrade() -> None:
    op.drop_constraint("difficulties_uid_key", "difficulties", type_="unique")
    op.drop_column("difficulties", "uid")
