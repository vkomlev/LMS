"""M1: users relax constraints + pgcrypto.

Revision ID: 20260428_010000_m1_users_relax
Revises: teacher_next_modes_stage39
Create Date: 2026-04-28

- CREATE EXTENSION pgcrypto (для gen_random_uuid() в user_session)
- users.password_hash: DROP NOT NULL
- users.email: DROP NOT NULL + заменить UNIQUE constraint на partial unique index
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260428_010000_m1_users_relax"
down_revision: Union[str, None] = "teacher_next_modes_stage39"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    op.alter_column("users", "password_hash", existing_type=sa.String(), nullable=True)

    op.drop_constraint("users_email_key", "users", type_="unique")
    op.alter_column("users", "email", existing_type=sa.String(), nullable=True)
    op.create_index(
        "users_email_unique_partial",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("users_email_unique_partial", table_name="users")
    op.execute(
        "UPDATE users SET email = id::text || '@placeholder.invalid' WHERE email IS NULL;"
    )
    op.execute("UPDATE users SET password_hash = '' WHERE password_hash IS NULL;")
    op.alter_column("users", "email", existing_type=sa.String(), nullable=False)
    op.alter_column("users", "password_hash", existing_type=sa.String(), nullable=False)
    op.create_unique_constraint("users_email_key", "users", ["email"])
    # pgcrypto оставляем (не вреден, DROP EXTENSION только вручную)
