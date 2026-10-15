"""tsk-1361 (фаза Б1): отметка свежести переопределений ученика.

`student_requirement_sync` — когда правила последний раз пересчитали строки
ученика в `student_requirement_override`. По ней быстрый тик (раз в минуту)
находит учеников, у которых с тех пор появились сдачи, проверки, пропуски,
зачёты материалов, правка плана объёма или правка содержимого их курсов, —
по данным, а не по вызовам из мест записи (такие вызовы дважды пропускали,
tsk-495, tsk-669).

Rollback: `alembic downgrade tsk1361_requirement_override` — таблица
снимается, быстрый тик считает всех учеников «несвежими» до первой сверки.

Revision ID: tsk1361_requirement_sync
Revises: tsk1361_requirement_override
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk1361_requirement_sync"
down_revision: Union[str, None] = "tsk1361_requirement_override"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Создать таблицу отметок свежести."""
    op.create_table(
        "student_requirement_sync",
        sa.Column(
            "student_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        comment="tsk-1361: момент последнего пересчёта переопределений ученика",
    )


def downgrade() -> None:
    """Снять таблицу."""
    op.drop_table("student_requirement_sync")
