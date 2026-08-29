"""tsk-718: внешние обращения, из которых заведён лид.

Зачем. Переписка с Авито будет сама заводить лида в мини-CRM кабинета
маркетолога: без этого воронка обрывается на обращении — видно, сколько людей
написали, и не видно, сколько из них стали учениками. Чтобы повторное
обращение того же человека не превращалось в второго лида, нужна память о том,
из какого внешнего обращения лид уже заведён.

Почему отдельная таблица, а не пара колонок в `leads`. У лида, заведённого
руками в кабинете, внешнего номера нет — колонки стояли бы пустыми. А в
PostgreSQL два NULL в уникальном ключе друг другу не противоречат: ключ с
пустой колонкой не дедуплицирует ничего и делает это молча (ровно так у
соседнего проекта таблица расходов выросла в 11 раз). Здесь обе колонки ключа
объявлены NOT NULL, поэтому уникальность действует всегда.

На поведение кабинета миграция не влияет: таблица после накатки пуста, лиды и
их правка работают ровно как вчера.

Rollback: `alembic downgrade tsk053_guest_quiz_lead`. Снимается только связь с
внешним обращением; сами лиды остаются на месте.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk718_lead_external_ref"
down_revision: Union[str, None] = "tsk053_guest_quiz_lead"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lead_external_ref",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "source",
            sa.Text(),
            nullable=False,
            comment="Система-источник, например avito_messenger",
        ),
        sa.Column(
            "external_id",
            sa.Text(),
            nullable=False,
            comment="Идентификатор человека во внешней системе — ключ склейки",
        ),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="lead_external_ref_pkey"),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["leads.id"],
            ondelete="CASCADE",
            name="lead_external_ref_lead_id_fkey",
        ),
        sa.UniqueConstraint("source", "external_id", name="uq_lead_external_ref"),
        comment="Внешние обращения, из которых заведён лид (tsk-718)",
    )
    op.create_index(
        "ix_lead_external_ref_lead_id", "lead_external_ref", ["lead_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_lead_external_ref_lead_id", table_name="lead_external_ref")
    op.drop_table("lead_external_ref")
