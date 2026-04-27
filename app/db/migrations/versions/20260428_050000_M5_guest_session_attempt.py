"""M5: таблицы guest_session + guest_attempt.

Revision ID: 20260428_050000_m5_guest_session_attempt
Revises: 20260428_040000_m4_audit_product_events
Create Date: 2026-04-28

- guest_session: UUID PK, ip, ua_fingerprint, attributed_user_id
- guest_attempt: BigSerial PK, FK guest_session, task_id, answer_json, attributed_user_id
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260428_050000_m5_guest"
down_revision: Union[str, None] = "20260428_040000_m4_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "guest_session",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("ip", postgresql.INET, nullable=True),
        sa.Column("ua_fingerprint", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributed_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    op.create_table(
        "guest_attempt",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "guest_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("guest_session.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Integer,
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("answer_json", postgresql.JSONB, nullable=True),
        sa.Column("is_correct", sa.Boolean, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributed_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("attributed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_guest_attempt_session",
        "guest_attempt",
        ["guest_session_id", "created_at"],
    )
    op.create_index(
        "idx_guest_attempt_unattributed",
        "guest_attempt",
        ["created_at"],
        postgresql_where=sa.text("attributed_user_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_guest_attempt_unattributed", table_name="guest_attempt")
    op.drop_index("idx_guest_attempt_session", table_name="guest_attempt")
    op.drop_table("guest_attempt")
    op.drop_table("guest_session")
