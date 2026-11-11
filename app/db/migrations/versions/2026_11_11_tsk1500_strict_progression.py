"""Жёсткая последовательность подкурсов — tsk-1500.

Revision ID: tsk1500_strict_progression
Revises: tsk1247_material_type_trainer
Create Date: 2026-11-11

`courses.strict_progression` — настройка КОРНЕВОГО курса: движок не отдаёт
элементы следующего подкурса, пока в предыдущих есть нерешённые обязательные
задания (накопительно). Выключена по умолчанию — без включения настройки
поведение платформы не меняется.

`progression_overrides` — ручные разблокировки методистом отдельных учеников
(болезнь, договорённость): пока запись на пару (корень, ученик) есть, гейт
для неё молчит.

Откат: DROP TABLE + DROP COLUMN; значения настроек теряются.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "tsk1500_strict_progression"
down_revision: Union[str, None] = "tsk1247_material_type_trainer"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "courses",
        sa.Column(
            "strict_progression",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment=(
                "tsk-1500: жёсткая последовательность подкурсов — next-item не "
                "отдаёт элементы следующего подкурса, пока в предыдущих есть "
                "нерешённые обязательные задания (накопительно)"
            ),
        ),
    )
    op.create_table(
        "progression_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "root_course_id",
            sa.Integer(),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "root_course_id", "user_id", name="uq_progression_overrides_pair"
        ),
    )
    op.create_index(
        "ix_progression_overrides_user",
        "progression_overrides",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_progression_overrides_user", table_name="progression_overrides")
    op.drop_table("progression_overrides")
    op.drop_column("courses", "strict_progression")
