"""Learning Engine V1, Stage 1: CHECK-ограничения на статусы и лимиты

Revision ID: learning_engine_stage1_checks
Revises: learning_engine_stage1
Create Date: 2026-02-25 11:00:00

- student_material_progress.status IN ('completed')
- student_course_state.state IN ('NOT_STARTED','IN_PROGRESS','COMPLETED','BLOCKED_DEPENDENCY')
- tasks.max_attempts IS NULL OR max_attempts > 0
"""
from typing import Sequence, Union

from alembic import op


revision: str = "learning_engine_stage1_checks"
down_revision: Union[str, None] = "learning_engine_stage1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "student_material_progress_status_check",
        "student_material_progress",
        "status IN ('completed')",
    )
    op.create_check_constraint(
        "student_course_state_state_check",
        "student_course_state",
        "state IN ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETED', 'BLOCKED_DEPENDENCY')",
    )
    op.create_check_constraint(
        "tasks_max_attempts_positive",
        "tasks",
        "max_attempts IS NULL OR max_attempts > 0",
    )


def downgrade() -> None:
    op.drop_constraint("tasks_max_attempts_positive", "tasks", type_="check")
    op.drop_constraint("student_course_state_state_check", "student_course_state", type_="check")
    op.drop_constraint("student_material_progress_status_check", "student_material_progress", type_="check")
