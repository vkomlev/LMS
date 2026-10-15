"""tsk-1361: персональная обязательность элемента с причиной.

`student_requirement_override` — отступление от `requirement_level` задания,
материала или целого узла курса для одного ученика. Строку пишет правило
(прощение нового tsk-692/1261/1294, сокращение программы tsk-798) или
человек (учитель). Отмена мягкая (`revoked_at`) — история причин остаётся.
Активной может быть одна строка на (ученик, вид, цель).

Фаза А (тень): таблицу заполняет фоновая сверка, потребители её ещё не
читают — поведение экранов не меняется.

Rollback: `alembic downgrade tsk994_final_check` — таблица снимается
целиком, потребители от неё на фазе А не зависят.

Revision ID: tsk1361_requirement_override
Revises: tsk994_final_check
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk1361_requirement_override"
down_revision: Union[str, None] = "tsk994_final_check"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Создать таблицу, ограничения и индексы."""
    op.create_table(
        "student_requirement_override",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "student_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("target_kind", sa.String(16), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(32), nullable=False),
        sa.Column("reason_text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_by", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked_by", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.CheckConstraint(
            "target_kind IN ('task', 'material', 'course')", name="ck_sro_target_kind"
        ),
        sa.CheckConstraint(
            "level IN ('required', 'recommended', 'skippable')", name="ck_sro_level"
        ),
        sa.CheckConstraint("source IN ('rule', 'human')", name="ck_sro_source"),
        sa.CheckConstraint(
            "source = 'human' OR created_by IS NULL", name="ck_sro_rule_has_no_author"
        ),
        comment="tsk-1361: персональная обязательность элемента с причиной",
    )
    op.create_index(
        "uq_sro_active_target",
        "student_requirement_override",
        ["student_id", "target_kind", "target_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_sro_target_active",
        "student_requirement_override",
        ["target_kind", "target_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    """Снять таблицу."""
    op.drop_index("ix_sro_target_active", table_name="student_requirement_override")
    op.drop_index("uq_sro_active_target", table_name="student_requirement_override")
    op.drop_table("student_requirement_override")
