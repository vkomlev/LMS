"""Add content requirement levels and skip progress.

Revision ID: tsk111_content_req_skip
Revises: tasks_order_position_triggers
Create Date: 2026-06-06 01:00:00
"""
from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "tsk111_content_req_skip"
down_revision: Union[str, None] = "tasks_order_position_triggers"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


REQUIREMENT_CHECK = "requirement_level IN ('skippable', 'recommended', 'required')"


def upgrade() -> None:
    op.add_column(
        "materials",
        sa.Column(
            "requirement_level",
            sa.String(length=16),
            nullable=False,
            server_default="required",
        ),
    )
    op.create_check_constraint(
        "materials_requirement_level_check",
        "materials",
        REQUIREMENT_CHECK,
    )

    op.add_column(
        "tasks",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "requirement_level",
            sa.String(length=16),
            nullable=False,
            server_default="required",
        ),
    )
    op.create_check_constraint(
        "tasks_requirement_level_check",
        "tasks",
        REQUIREMENT_CHECK,
    )

    op.drop_constraint(
        "student_material_progress_status_check",
        "student_material_progress",
        type_="check",
    )
    op.add_column(
        "student_material_progress",
        sa.Column("skipped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "student_material_progress_status_check",
        "student_material_progress",
        "status IN ('completed', 'skipped')",
    )

    op.create_table(
        "student_task_progress",
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="skipped",
        ),
        sa.Column(
            "skipped_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="student_task_progress_student_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="CASCADE",
            name="student_task_progress_task_id_fkey",
        ),
        sa.PrimaryKeyConstraint(
            "student_id",
            "task_id",
            name="student_task_progress_pkey",
        ),
        sa.CheckConstraint(
            "status = 'skipped'",
            name="student_task_progress_status_check",
        ),
    )
    op.create_index(
        "idx_student_task_progress_student_status",
        "student_task_progress",
        ["student_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_student_task_progress_student_status",
        table_name="student_task_progress",
    )
    op.drop_table("student_task_progress")

    op.drop_constraint(
        "student_material_progress_status_check",
        "student_material_progress",
        type_="check",
    )
    op.drop_column("student_material_progress", "skipped_at")
    op.create_check_constraint(
        "student_material_progress_status_check",
        "student_material_progress",
        "status = 'completed'",
    )

    op.drop_constraint("tasks_requirement_level_check", "tasks", type_="check")
    op.drop_column("tasks", "requirement_level")
    op.drop_column("tasks", "is_active")

    op.drop_constraint("materials_requirement_level_check", "materials", type_="check")
    op.drop_column("materials", "requirement_level")
