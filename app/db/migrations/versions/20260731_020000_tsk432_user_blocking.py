"""tsk-432: блокировка учётной записи — отдельно от слияния.

Почему НЕ переиспользуется `users.is_active`: этот флаг уже занят и означает
«учётка слита в другую» (рядом лежит `merged_into_user_id`, и списки скрывают
такие записи). Заблокированный человек — не слитый: он должен остаться видимым
преподавателю и методисту, со своей историей и работами, просто без входа.
Смешать два состояния в одном флаге — значит потерять ответ на вопрос «почему
этого человека не видно».

Отдельно: сам по себе флаг ничего не закрывает. Вход проверяется в
`get_current_user` и на четырёх путях авторизации — там и стоит проверка.

Revision ID: tsk432_user_blocking
Revises: tsk492_occ_teacher_one_off
Create Date: 2026-07-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk432_user_blocking"
down_revision: Union[str, None] = "tsk492_occ_teacher_one_off"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавить признаки блокировки."""
    op.add_column(
        "users",
        sa.Column(
            "blocked_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Когда закрыт вход. NULL — доступ открыт",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "blocked_reason",
            sa.Text(),
            nullable=True,
            comment="Почему закрыт вход — видно администратору в карточке",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "blocked_by_user_id",
            sa.Integer(),
            nullable=True,
            comment="Кто закрыл вход",
        ),
    )
    op.create_foreign_key(
        "users_blocked_by_user_id_fkey",
        "users",
        "users",
        ["blocked_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Заблокированных единицы, а спрашивают о них на каждом запросе
    # (`get_current_user`) — частичный индекс дешевле полного.
    op.create_index(
        "ix_users_blocked",
        "users",
        ["id"],
        postgresql_where=sa.text("blocked_at IS NOT NULL"),
    )


def downgrade() -> None:
    """Снять признаки; блокировки при этом теряются — вход откроется всем."""
    op.drop_index("ix_users_blocked", table_name="users")
    op.drop_constraint("users_blocked_by_user_id_fkey", "users", type_="foreignkey")
    op.drop_column("users", "blocked_by_user_id")
    op.drop_column("users", "blocked_reason")
    op.drop_column("users", "blocked_at")
