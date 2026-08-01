"""tsk-498: ссылки доступа родителя к дашборду ученика (без регистрации).

Поверх tsk-478 (кабинет родителя со входом по magic-link). Оператор:
вход по почте слишком сложен для родителей — нужна ссылка-пропуск, которую
оператор выдаёт лично. Ссылка НЕ даёт сессию под аккаунтом: токен открывает
РОВНО один read-only эндпоинт дашборда конкретного ученика.

В БД хранится ХЕШ токена (sha256), как у `magic_link`/`user_session` — сырой
токен показывается один раз в ответе на создание и больше нигде не восстановим.

Решения оператора 2026-08-01: бессрочно (гаснет только ручным отзывом),
несколько активных ссылок на одного ученика (маме и папе отдельно, отзываются
независимо).

Revision ID: tsk498_parent_access_links
Revises: tsk478_parent_role_and_links
Create Date: 2026-08-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk498_parent_access_links"
down_revision: Union[str, None] = "tsk478_parent_role_and_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "parent_access_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "token_hash", sa.LargeBinary(), nullable=False,
            comment="sha256 сырого токена — сам токен не хранится",
        ),
        sa.Column("student_id", sa.Integer(), nullable=False, comment="Чей дашборд открывает ссылка"),
        sa.Column(
            "label", sa.Text(), nullable=True,
            comment="Кому выдана — «мама», «папа»; только для оператора",
        ),
        sa.Column(
            "created_by_user_id", sa.Integer(), nullable=True,
            comment="Кто выдал ссылку (NULL — сервисный ключ)",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "revoked_at", sa.DateTime(timezone=True), nullable=True,
            comment="Когда отозвана. NULL — ссылка действует (срока годности нет)",
        ),
        sa.Column(
            "last_used_at", sa.DateTime(timezone=True), nullable=True,
            comment="Когда по ссылке последний раз открывали дашборд",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="parent_access_links_student_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL",
            name="parent_access_links_created_by_user_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="parent_access_links_pkey"),
        sa.UniqueConstraint("token_hash", name="uq_parent_access_links_token_hash"),
        comment="Ссылки доступа родителя к дашборду ученика (tsk-498)",
    )
    # Поиск по ученику — список ссылок в карточке; по token_hash уже есть
    # уникальный индекс от UniqueConstraint (он и обслуживает резолв токена).
    op.create_index(
        "ix_parent_access_links_student", "parent_access_links", ["student_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_parent_access_links_student", table_name="parent_access_links")
    op.drop_table("parent_access_links")
