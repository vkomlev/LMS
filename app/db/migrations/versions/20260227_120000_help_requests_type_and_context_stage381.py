"""Learning Engine V1, этап 3.8.1: типизация заявок (request_type, auto_created, context_json)

Revision ID: help_requests_stage381
Revises: help_requests_stage38
Create Date: 2026-02-27 12:00:00

- help_requests: request_type, auto_created, context_json
- Индексы по (status, request_type, updated_at) и (assigned_teacher_id, status, request_type, updated_at)
- Уникальный частичный индекс для антидубля open blocked_limit по (student_id, task_id)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "help_requests_stage381"
down_revision: Union[str, None] = "help_requests_stage38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "help_requests",
        sa.Column("request_type", sa.String(32), nullable=False, server_default=sa.text("'manual_help'")),
    )
    op.add_column(
        "help_requests",
        sa.Column("auto_created", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "help_requests",
        sa.Column("context_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_check_constraint(
        "help_requests_request_type_check",
        "help_requests",
        "request_type IN ('manual_help', 'blocked_limit')",
    )

    op.create_index(
        "idx_help_requests_status_type_updated",
        "help_requests",
        ["status", "request_type", "updated_at"],
        unique=False,
        postgresql_ops={"updated_at": "DESC"},
    )
    op.create_index(
        "idx_help_requests_assigned_status_type_updated",
        "help_requests",
        ["assigned_teacher_id", "status", "request_type", "updated_at"],
        unique=False,
        postgresql_ops={"updated_at": "DESC"},
    )
    op.execute("""
        CREATE UNIQUE INDEX uniq_help_requests_open_blocked_limit_student_task
        ON help_requests (student_id, task_id, request_type)
        WHERE status = 'open' AND request_type = 'blocked_limit'
    """)


def downgrade() -> None:
    op.drop_index(
        "uniq_help_requests_open_blocked_limit_student_task",
        table_name="help_requests",
    )
    op.drop_index("idx_help_requests_assigned_status_type_updated", table_name="help_requests")
    op.drop_index("idx_help_requests_status_type_updated", table_name="help_requests")
    op.drop_constraint("help_requests_request_type_check", "help_requests", type_="check")
    op.drop_column("help_requests", "context_json")
    op.drop_column("help_requests", "auto_created")
    op.drop_column("help_requests", "request_type")
