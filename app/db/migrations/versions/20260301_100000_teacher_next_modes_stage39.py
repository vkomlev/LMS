"""Learning Engine V1, этап 3.9: Teacher Next Modes — claim/lock и SLA для help_requests и task_results.

Revision ID: teacher_next_modes_stage39
Revises: help_requests_stage381
Create Date: 2026-03-01

- help_requests: priority, due_at, claimed_by, claim_token, claim_expires_at + индексы
- task_results: review_claimed_by, review_claim_token, review_claim_expires_at + индекс
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
revision: str = "teacher_next_modes_stage39"
down_revision: Union[str, None] = "help_requests_stage381"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----- help_requests: priority, due_at, claim -----
    op.add_column(
        "help_requests",
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default=sa.text("100")),
    )
    op.add_column(
        "help_requests",
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "help_requests",
        sa.Column("claimed_by", sa.Integer(), nullable=True),
    )
    op.add_column(
        "help_requests",
        sa.Column("claim_token", sa.String(64), nullable=True),
    )
    op.add_column(
        "help_requests",
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "help_requests_claimed_by_fkey",
        "help_requests",
        "users",
        ["claimed_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_help_requests_status_type_priority_created",
        "help_requests",
        ["status", "request_type", "priority", "created_at"],
        unique=False,
        postgresql_ops={"priority": "ASC", "created_at": "ASC"},
    )
    op.create_index(
        "idx_help_requests_claimed_expires",
        "help_requests",
        ["claimed_by", "claim_expires_at"],
        unique=False,
    )
    op.create_index(
        "idx_help_requests_status_claim_expires",
        "help_requests",
        ["status", "claim_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'open'"),
    )

    # ----- task_results: review claim -----
    op.add_column(
        "task_results",
        sa.Column("review_claimed_by", sa.Integer(), nullable=True),
    )
    op.add_column(
        "task_results",
        sa.Column("review_claim_token", sa.String(64), nullable=True),
    )
    op.add_column(
        "task_results",
        sa.Column("review_claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "task_results_review_claimed_by_fkey",
        "task_results",
        "users",
        ["review_claimed_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_task_results_checked_claim_submitted",
        "task_results",
        ["checked_at", "review_claim_expires_at", "submitted_at"],
        unique=False,
        postgresql_where=sa.text("checked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_task_results_checked_claim_submitted",
        table_name="task_results",
    )
    op.drop_constraint("task_results_review_claimed_by_fkey", "task_results", type_="foreignkey")
    op.drop_column("task_results", "review_claim_expires_at")
    op.drop_column("task_results", "review_claim_token")
    op.drop_column("task_results", "review_claimed_by")

    op.drop_index("idx_help_requests_status_claim_expires", table_name="help_requests")
    op.drop_index("idx_help_requests_claimed_expires", table_name="help_requests")
    op.drop_index("idx_help_requests_status_type_priority_created", table_name="help_requests")
    op.drop_constraint("help_requests_claimed_by_fkey", "help_requests", type_="foreignkey")
    op.drop_column("help_requests", "claim_expires_at")
    op.drop_column("help_requests", "claim_token")
    op.drop_column("help_requests", "claimed_by")
    op.drop_column("help_requests", "due_at")
    op.drop_column("help_requests", "priority")
