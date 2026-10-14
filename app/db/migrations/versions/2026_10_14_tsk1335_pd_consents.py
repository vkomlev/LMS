"""tsk-1335: журнал согласий на обработку ПДн (152-ФЗ).

Таблица `pd_consents` — только дозапись: кто заполнил форму (родитель /
взрослый / подросток 14–17), на какие редакции согласия и политики, из какой
формы, с каким лидом / пользователем / гостевой сессией связано. Связи —
SET NULL: удаление лида или пользователя не стирает факт согласия.

`magic_link.pd_consent_id` — согласие, данное при запросе ссылки на новый
адрес; при создании пользователя на verify строке журнала проставляется
`user_id`.

Rollback: `alembic downgrade tsk993_help_stuck_note` — снимается колонка
`magic_link.pd_consent_id` и таблица целиком (журнал согласий теряется —
перед откатом на проде выгрузить его).

Revision ID: tsk1335_pd_consents
Revises: tsk993_help_stuck_note
Create Date: 2026-10-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk1335_pd_consents"
down_revision: Union[str, None] = "tsk993_help_stuck_note"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Таблица журнала с CHECK на роль и источник, индексы, ссылка из magic_link."""
    op.create_table(
        "pd_consents",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("filler_role", sa.Text(), nullable=False),
        sa.Column("doc_consent_version", sa.Text(), nullable=False),
        sa.Column("doc_policy_version", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("page", sa.Text(), nullable=True),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("guest_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "email", sa.Text(), nullable=True,
            comment="Только для magic_link до создания пользователя",
        ),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pd_consents_pkey"),
        sa.CheckConstraint(
            "filler_role IN ('parent', 'adult', 'teen_14_17')",
            name="ck_pd_consents_filler_role",
        ),
        sa.CheckConstraint(
            "source IN ('landing', 'quiz_funnel', 'quiz', 'diagnostic', "
            "'magic_link', 'vk', 'tg', 'integration')",
            name="ck_pd_consents_source",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], ondelete="SET NULL", name="pd_consents_lead_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="SET NULL", name="pd_consents_user_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["guest_session_id"], ["guest_session.id"], ondelete="SET NULL",
            name="pd_consents_guest_session_id_fkey",
        ),
        comment="Журнал согласий на обработку ПДн, только дозапись (tsk-1335)",
    )
    op.create_index("ix_pd_consents_user_id", "pd_consents", ["user_id"])
    op.create_index("ix_pd_consents_lead_id", "pd_consents", ["lead_id"])

    op.add_column(
        "magic_link",
        sa.Column(
            "pd_consent_id", sa.BigInteger(), nullable=True,
            comment="Согласие на обработку ПДн, данное при запросе ссылки (tsk-1335)",
        ),
    )
    op.create_foreign_key(
        "magic_link_pd_consent_id_fkey", "magic_link", "pd_consents",
        ["pd_consent_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    """Снять ссылку из magic_link, индексы и таблицу журнала."""
    op.drop_constraint("magic_link_pd_consent_id_fkey", "magic_link", type_="foreignkey")
    op.drop_column("magic_link", "pd_consent_id")
    op.drop_index("ix_pd_consents_lead_id", table_name="pd_consents")
    op.drop_index("ix_pd_consents_user_id", table_name="pd_consents")
    op.drop_table("pd_consents")
