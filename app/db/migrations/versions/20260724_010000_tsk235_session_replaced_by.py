"""tsk-235: replaced_by_session_id в user_session (окно благодати ротации refresh).

Revision ID: tsk235_session_replaced_by
Revises: tsk381_difficulty_provenance
Create Date: 2026-07-24

Проблема: две вкладки SPW делят одну refresh-cookie. Ротация refresh-токена
отзывает старую сессию мгновенно, без окна благодати — конкурентный refresh
из второй вкладки получает 401 ("Не удалось сохранить"). Фикс — идемпотентный
повтор в окне благодати требует знать, какая сессия сменила отозванную.

- user_session.replaced_by_session_id: self-ref FK на новую сессию, которая
  заместила эту при ротации. NULL — сессия отозвана не через ротацию (logout,
  revoke_all_sessions) либо ещё активна.
- ON DELETE SET NULL: строки user_session не удаляются штатным кодом (только
  revoked_at), но self-ref FK не должен блокировать гипотетическую ручную
  чистку старых строк.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk235_session_replaced_by"
down_revision: Union[str, None] = "tsk381_difficulty_provenance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_session",
        sa.Column(
            "replaced_by_session_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_user_session_replaced_by",
        "user_session",
        "user_session",
        ["replaced_by_session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_user_session_replaced_by",
        "user_session",
        ["replaced_by_session_id"],
        postgresql_where=sa.text("replaced_by_session_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_user_session_replaced_by", table_name="user_session")
    op.drop_constraint("fk_user_session_replaced_by", "user_session", type_="foreignkey")
    op.drop_column("user_session", "replaced_by_session_id")
