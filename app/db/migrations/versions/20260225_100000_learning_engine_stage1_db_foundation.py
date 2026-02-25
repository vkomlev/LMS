"""Learning Engine V1, Stage 1: DB foundation

Revision ID: learning_engine_stage1
Revises: add_difficulties_uid
Create Date: 2026-02-25 10:00:00

- user_courses: is_active boolean not null default true
- Новые таблицы: student_material_progress, student_task_limit_override, student_course_state, learning_events
- tasks: max_attempts, time_limit_sec
- attempts: time_expired
- Индексы под рабочие выборки и уникальность
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "learning_engine_stage1"
down_revision: Union[str, None] = "add_difficulties_uid"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) user_courses: is_active
    op.add_column(
        "user_courses",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    # 2) student_material_progress
    op.create_table(
        "student_material_progress",
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE", name="student_material_progress_student_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["material_id"], ["materials.id"], ondelete="CASCADE", name="student_material_progress_material_id_fkey"
        ),
        sa.PrimaryKeyConstraint("student_id", "material_id", name="student_material_progress_pkey"),
    )
    op.create_index(
        "idx_student_material_progress_student_status",
        "student_material_progress",
        ["student_id", "status"],
        unique=False,
    )

    # 3) student_task_limit_override
    op.create_table(
        "student_task_limit_override",
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("max_attempts_override", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("max_attempts_override > 0", name="student_task_limit_override_max_attempts_positive"),
        sa.ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE", name="student_task_limit_override_student_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], ondelete="CASCADE", name="student_task_limit_override_task_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], ondelete="SET NULL", name="student_task_limit_override_updated_by_fkey"
        ),
        sa.PrimaryKeyConstraint("student_id", "task_id", name="student_task_limit_override_pkey"),
    )

    # 4) student_course_state
    op.create_table(
        "student_course_state",
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE", name="student_course_state_student_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["course_id"], ["courses.id"], ondelete="CASCADE", name="student_course_state_course_id_fkey"
        ),
        sa.PrimaryKeyConstraint("student_id", "course_id", name="student_course_state_pkey"),
    )
    op.create_index(
        "idx_student_course_state_student_state",
        "student_course_state",
        ["student_id", "state"],
        unique=False,
    )

    # 5) learning_events
    op.create_table(
        "learning_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE", name="learning_events_student_id_fkey"
        ),
        sa.PrimaryKeyConstraint("id", name="learning_events_pkey"),
    )
    op.create_index(
        "idx_learning_events_student_created",
        "learning_events",
        ["student_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_learning_events_type_created",
        "learning_events",
        ["event_type", "created_at"],
        unique=False,
    )

    # 6) tasks: max_attempts, time_limit_sec
    op.add_column(
        "tasks",
        sa.Column("max_attempts", sa.Integer(), nullable=True, comment="Лимит попыток (null => default на уровне сервиса)"),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "time_limit_sec",
            sa.Integer(),
            nullable=True,
            comment="Лимит времени на попытку в секундах",
        ),
    )
    op.create_check_constraint(
        "tasks_time_limit_sec_positive",
        "tasks",
        "time_limit_sec IS NULL OR time_limit_sec > 0",
    )

    # 7) attempts: time_expired
    op.add_column(
        "attempts",
        sa.Column(
            "time_expired",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="Попытка завершена по таймауту",
        ),
    )


def downgrade() -> None:
    op.drop_column("attempts", "time_expired")
    op.drop_constraint("tasks_time_limit_sec_positive", "tasks", type_="check")
    op.drop_column("tasks", "time_limit_sec")
    op.drop_column("tasks", "max_attempts")

    op.drop_index("idx_learning_events_type_created", table_name="learning_events")
    op.drop_index("idx_learning_events_student_created", table_name="learning_events")
    op.drop_table("learning_events")

    op.drop_index("idx_student_course_state_student_state", table_name="student_course_state")
    op.drop_table("student_course_state")

    op.drop_table("student_task_limit_override")

    op.drop_index("idx_student_material_progress_student_status", table_name="student_material_progress")
    op.drop_table("student_material_progress")

    op.drop_column("user_courses", "is_active")
