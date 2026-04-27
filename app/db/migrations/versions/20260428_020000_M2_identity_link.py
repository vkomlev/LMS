"""M2: таблица identity_link.

Revision ID: 20260428_020000_m2_identity_link
Revises: 20260428_010000_m1_users_relax
Create Date: 2026-04-28

- CREATE TABLE identity_link (id, user_id FK users, kind, value, vk tokens, timestamps)
- UNIQUE(kind, value), CHECK kind IN ('email','tg','vk')
- Backfill из users.tg_id и users.email
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260428_020000_m2_identity_link"
down_revision: Union[str, None] = "20260428_010000_m1_users_relax"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "identity_link",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("value", sa.String(255), nullable=False),
        sa.Column("vk_access_token_enc", sa.LargeBinary, nullable=True),
        sa.Column("vk_refresh_token_enc", sa.LargeBinary, nullable=True),
        sa.Column("vk_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('email', 'tg', 'vk')", name="identity_link_kind_check"),
    )
    op.create_unique_constraint(
        "uq_identity_link_kind_value", "identity_link", ["kind", "value"]
    )
    op.create_index("idx_identity_link_user_id", "identity_link", ["user_id"])

    # Backfill из users.tg_id
    op.execute(
        """
        INSERT INTO identity_link (user_id, kind, value, created_at)
        SELECT id, 'tg', tg_id::text, created_at
        FROM users
        WHERE tg_id IS NOT NULL
        ON CONFLICT (kind, value) DO NOTHING;
        """
    )

    # Backfill из users.email
    op.execute(
        """
        INSERT INTO identity_link (user_id, kind, value, created_at)
        SELECT id, 'email', lower(email), created_at
        FROM users
        WHERE email IS NOT NULL
        ON CONFLICT (kind, value) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.drop_index("idx_identity_link_user_id", table_name="identity_link")
    op.drop_constraint("uq_identity_link_kind_value", "identity_link", type_="unique")
    op.drop_table("identity_link")
