"""tsk-930: admin-выдача ссылки входа ученику напрямую по user_id.

Оба обычных пути входа (ВК, magic-link на почту) требуют идентификатора —
адреса или привязанного ВК-аккаунта. У части учеников их нет вовсе. Решение
оператора (13.09): не заводить служебный email, а расширить `magic_link` —
токен может быть привязан либо к email (как раньше), либо напрямую к
`user_id` (admin-выдача, минуя email/identity_link).

Новые колонки:
- `user_id` — если задан, verify резолвит пользователя НАПРЯМУЮ по этому id,
  минуя `get_or_create_user_by_email`. NULL у всех обычных email-ссылок.
- `issued_by_user_id` — кто из персонала выдал ссылку вручную (admin). NULL у
  обычных писем — там некому не быть человеком, это самообслуживание.

`email` становится nullable: у admin-выданной ссылки может не быть почты
вовсе (ровно случай задачи — ученик без email-identity).

Revision ID: tsk930_admin_login_link
Revises: tsk923_schedule_preference_ack
Create Date: 2026-09-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk930_admin_login_link"
down_revision: Union[str, None] = "tsk923_schedule_preference_ack"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("magic_link", "email", existing_type=sa.String(length=255), nullable=True)
    op.add_column(
        "magic_link",
        sa.Column(
            "user_id", sa.Integer(), nullable=True,
            comment="Admin-выдача напрямую по user_id, минуя email/identity_link (tsk-930)",
        ),
    )
    op.add_column(
        "magic_link",
        sa.Column(
            "issued_by_user_id", sa.Integer(), nullable=True,
            comment="Кто из персонала выдал ссылку вручную (admin); NULL — обычный email-flow",
        ),
    )
    op.create_foreign_key(
        "magic_link_user_id_fkey", "magic_link", "users",
        ["user_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "magic_link_issued_by_user_id_fkey", "magic_link", "users",
        ["issued_by_user_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_magic_link_user_id", "magic_link", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_magic_link_user_id", table_name="magic_link")
    op.drop_constraint("magic_link_issued_by_user_id_fkey", "magic_link", type_="foreignkey")
    op.drop_constraint("magic_link_user_id_fkey", "magic_link", type_="foreignkey")
    op.drop_column("magic_link", "issued_by_user_id")
    op.drop_column("magic_link", "user_id")
    op.alter_column("magic_link", "email", existing_type=sa.String(length=255), nullable=False)
