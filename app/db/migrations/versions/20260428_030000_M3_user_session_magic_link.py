"""M3: таблицы user_session + magic_link.

Revision ID: 20260428_030000_m3_user_session_magic_link
Revises: 20260428_020000_m2_identity_link
Create Date: 2026-04-28

- user_session: UUID PK (gen_random_uuid), token_hash BYTEA UNIQUE, TTL columns, revoked_at
- magic_link: одноразовые email-ссылки (token_hash BYTEA UNIQUE, expires_at, consumed_at)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260428_030000_m3_sessions"
down_revision: Union[str, None] = "20260428_020000_m2_identity_link"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_session",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.LargeBinary, nullable=False),
        sa.Column("refresh_token_hash", sa.LargeBinary, nullable=True),
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
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_user_session_token_hash", "user_session", ["token_hash"], unique=True
    )
    op.create_index(
        "idx_user_session_user_active",
        "user_session",
        ["user_id", "expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "magic_link",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("token_hash", sa.LargeBinary, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("uq_magic_link_token_hash", "magic_link", ["token_hash"], unique=True)
    op.create_index(
        "idx_magic_link_email_created", "magic_link", ["email", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_magic_link_email_created", table_name="magic_link")
    op.drop_index("uq_magic_link_token_hash", table_name="magic_link")
    op.drop_table("magic_link")
    op.drop_index("idx_user_session_user_active", table_name="user_session")
    op.drop_index("uq_user_session_token_hash", table_name="user_session")
    op.drop_table("user_session")
