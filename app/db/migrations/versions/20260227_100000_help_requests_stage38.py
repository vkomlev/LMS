"""Learning Engine V1, этап 3.8: заявки на помощь (teacher help-requests)

Revision ID: help_requests_stage38
Revises: hint_open_index_stage36
Create Date: 2026-02-27 10:00:00

- Таблицы: help_requests, help_request_replies
- Индексы и CHECK по ТЗ этапа 3.8
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "help_requests_stage38"
down_revision: Union[str, None] = "hint_open_index_stage36"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "help_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'open'")),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("attempt_id", sa.Integer(), nullable=True),
        sa.Column("event_id", sa.BigInteger(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("thread_id", sa.Integer(), nullable=True),
        sa.Column("assigned_teacher_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.Integer(), nullable=True),
        sa.Column("resolution_comment", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('open', 'closed')", name="help_requests_status_check"),
        sa.ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE", name="help_requests_student_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], ondelete="CASCADE", name="help_requests_task_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["course_id"], ["courses.id"], ondelete="SET NULL", name="help_requests_course_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["attempts.id"], ondelete="SET NULL", name="help_requests_attempt_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["learning_events.id"], ondelete="SET NULL", name="help_requests_event_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["messages.id"], ondelete="SET NULL", name="help_requests_thread_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["assigned_teacher_id"], ["users.id"], ondelete="SET NULL", name="help_requests_assigned_teacher_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["closed_by"], ["users.id"], ondelete="SET NULL", name="help_requests_closed_by_fkey"
        ),
        sa.PrimaryKeyConstraint("id", name="help_requests_pkey"),
    )
    op.create_index(
        "idx_help_requests_status_updated",
        "help_requests",
        ["status", "updated_at"],
        unique=False,
        postgresql_ops={"updated_at": "DESC"},
    )
    op.create_index(
        "idx_help_requests_assigned_status",
        "help_requests",
        ["assigned_teacher_id", "status", "updated_at"],
        unique=False,
        postgresql_ops={"updated_at": "DESC"},
    )
    op.create_index(
        "idx_help_requests_student_created",
        "help_requests",
        ["student_id", "created_at"],
        unique=False,
        postgresql_ops={"created_at": "DESC"},
    )
    op.create_index(
        "idx_help_requests_task_created",
        "help_requests",
        ["task_id", "created_at"],
        unique=False,
        postgresql_ops={"created_at": "DESC"},
    )
    op.create_index("idx_help_requests_event_id", "help_requests", ["event_id"], unique=False)

    op.create_table(
        "help_request_replies",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.BigInteger(), nullable=False),
        sa.Column("teacher_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("close_after_reply", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.ForeignKeyConstraint(
            ["request_id"], ["help_requests.id"], ondelete="CASCADE", name="help_request_replies_request_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"], ["users.id"], ondelete="CASCADE", name="help_request_replies_teacher_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["messages.id"], ondelete="CASCADE", name="help_request_replies_message_id_fkey"
        ),
        sa.PrimaryKeyConstraint("id", name="help_request_replies_pkey"),
    )
    op.create_index(
        "idx_help_request_replies_request_created",
        "help_request_replies",
        ["request_id", "created_at"],
        unique=False,
        postgresql_ops={"created_at": "DESC"},
    )
    op.execute("""
        CREATE UNIQUE INDEX uq_help_request_replies_req_idem
        ON help_request_replies (request_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_index("uq_help_request_replies_req_idem", table_name="help_request_replies")
    op.drop_index("idx_help_request_replies_request_created", table_name="help_request_replies")
    op.drop_table("help_request_replies")
    op.drop_index("idx_help_requests_event_id", table_name="help_requests")
    op.drop_index("idx_help_requests_task_created", table_name="help_requests")
    op.drop_index("idx_help_requests_student_created", table_name="help_requests")
    op.drop_index("idx_help_requests_assigned_status", table_name="help_requests")
    op.drop_index("idx_help_requests_status_updated", table_name="help_requests")
    op.drop_table("help_requests")
