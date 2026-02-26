"""Learning Engine V1, этап 3.5: аннулирование попытки (attempt cancel)

Revision ID: attempt_cancel_stage35
Revises: learning_engine_stage1_checks
Create Date: 2026-02-26 10:00:00

- attempts: cancelled_at (timestamptz null), cancel_reason (text null)
- Индекс для активных попыток (finished_at IS NULL AND cancelled_at IS NULL)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "attempt_cancel_stage35"
down_revision: Union[str, None] = "learning_engine_stage1_checks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "attempts",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True, comment="Время аннулирования попытки (этап 3.5)"),
    )
    op.add_column(
        "attempts",
        sa.Column("cancel_reason", sa.Text(), nullable=True, comment="Причина аннулирования (опционально)"),
    )
    op.create_index(
        "idx_attempts_active",
        "attempts",
        ["user_id", "course_id"],
        postgresql_where=sa.text("finished_at IS NULL AND cancelled_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_attempts_active", table_name="attempts")
    op.drop_column("attempts", "cancel_reason")
    op.drop_column("attempts", "cancelled_at")
