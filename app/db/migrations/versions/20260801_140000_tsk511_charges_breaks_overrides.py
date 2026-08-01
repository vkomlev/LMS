"""tsk-511/512/513: помесячные начисления, перерывы ученика, ручная цена.

Четыре новые таблицы и одно расширение существующего CHECK.

Расширение CHECK на `lesson_occurrence_participant.status` — единственное касание
живой таблицы. Оно добавляет значение `on_break` к уже разрешённым и ничего не
переписывает: перерыв гасит занятия ученика именно этим статусом, чтобы отличать
«не придёт по перерыву» от «отказался» и «не пришёл».

Rollback: `alembic downgrade tsk505_pricing_and_leads` — сносит четыре таблицы
целиком. ВНИМАНИЕ: вместе с ними пропадут все заведённые перерывы, ручные цены и
ВСЯ история начислений, включая закрытые месяцы; восстановить их будет неоткуда.
Перед откатом на проде — снять дамп четырёх таблиц. Откат также возвращает CHECK
к прежнему набору значений, поэтому сперва снимает статус `on_break` обратно в
`scheduled` — иначе старый CHECK не встанет на живые строки.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "tsk511_charges_breaks"
down_revision = "tsk505_pricing_and_leads"
branch_labels = None
depends_on = None

_PARTICIPANT_STATUSES_OLD = (
    "scheduled",
    "confirmed",
    "declined",
    "rescheduled",
    "no_show",
    "completed",
)
_PARTICIPANT_STATUSES_NEW = _PARTICIPANT_STATUSES_OLD + ("on_break",)


def _status_check(values: tuple[str, ...]) -> str:
    inner = ", ".join(f"'{v}'" for v in values)
    return f"status = ANY (ARRAY[{inner}]::text[])"


def upgrade() -> None:
    op.create_table(
        "student_break",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "student_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Границы включительные: перерыв «с 10 по 24» покрывает и 10-е, и 24-е.
        sa.Column("starts_on", sa.Date, nullable=False),
        sa.Column("ends_on", sa.Date, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column(
            "created_by",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("ends_on >= starts_on", name="ck_student_break_range"),
    )
    op.create_index("ix_student_break_student", "student_break", ["student_id", "starts_on"])

    op.create_table(
        "student_price_override",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "student_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            sa.Integer,
            sa.ForeignKey("pricing_group.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("price_minor", sa.Integer, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column(
            "created_by",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("price_minor >= 0", name="ck_student_price_override_non_negative"),
        sa.UniqueConstraint("student_id", "group_id", name="uq_student_price_override"),
    )

    op.create_table(
        "student_monthly_charge",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "student_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            sa.Integer,
            sa.ForeignKey("pricing_group.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Всегда первое число месяца — период, а не дата события.
        sa.Column("period", sa.Date, nullable=False),
        sa.Column("calculated_minor", sa.Integer, nullable=False),
        # Ручная сумма именно на этот месяц; ручная цена группы живёт отдельно.
        sa.Column("manual_minor", sa.Integer, nullable=True),
        sa.Column("expected_lessons", sa.Integer, nullable=False, server_default="0"),
        sa.Column("break_lessons", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "closed_by",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('open', 'closed')", name="ck_monthly_charge_status"),
        sa.CheckConstraint("calculated_minor >= 0", name="ck_monthly_charge_calculated_non_negative"),
        sa.CheckConstraint(
            "manual_minor IS NULL OR manual_minor >= 0",
            name="ck_monthly_charge_manual_non_negative",
        ),
        sa.CheckConstraint(
            "date_trunc('month', period::timestamp)::date = period",
            name="ck_monthly_charge_period_is_month_start",
        ),
        # Закрытый месяц обязан помнить, кем и когда закрыт: иначе «заморожено»
        # становится состоянием без следа.
        sa.CheckConstraint(
            "(status = 'closed') = (closed_at IS NOT NULL)",
            name="ck_monthly_charge_closed_has_timestamp",
        ),
        sa.UniqueConstraint("student_id", "group_id", "period", name="uq_monthly_charge"),
    )
    op.create_index(
        "ix_monthly_charge_period_status", "student_monthly_charge", ["period", "status"]
    )

    op.create_table(
        "charge_adjustment",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "student_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            sa.Integer,
            sa.ForeignKey("pricing_group.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Месяц, К КОТОРОМУ поправка применяется.
        sa.Column("period", sa.Date, nullable=False),
        # Со знаком: перенос может быть и в минус, и в плюс.
        sa.Column("amount_minor", sa.Integer, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        # Месяц, ИЗ которого пришёл перенос. Для ручной поправки пуст.
        sa.Column("origin_period", sa.Date, nullable=True),
        sa.Column(
            "created_by",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "source IN ('carry_forward', 'manual')", name="ck_charge_adjustment_source"
        ),
        sa.CheckConstraint(
            "(source = 'carry_forward') = (origin_period IS NOT NULL)",
            name="ck_charge_adjustment_origin",
        ),
    )
    # Перенос из одного и того же месяца не задваивается, сколько бы раз ни
    # прогнали пересчёт. Ручных поправок может быть сколько угодно.
    op.create_index(
        "uq_charge_adjustment_carry",
        "charge_adjustment",
        ["student_id", "group_id", "period", "origin_period"],
        unique=True,
        postgresql_where=sa.text("source = 'carry_forward'"),
    )
    op.create_index(
        "ix_charge_adjustment_lookup", "charge_adjustment", ["student_id", "group_id", "period"]
    )

    op.drop_constraint(
        "lesson_occurrence_participant_status_check",
        "lesson_occurrence_participant",
        type_="check",
    )
    op.create_check_constraint(
        "lesson_occurrence_participant_status_check",
        "lesson_occurrence_participant",
        _status_check(_PARTICIPANT_STATUSES_NEW),
    )


def downgrade() -> None:
    # Сперва снять `on_break` — иначе прежний CHECK не встанет на живые строки.
    op.execute(
        "UPDATE lesson_occurrence_participant SET status = 'scheduled' "
        "WHERE status = 'on_break'"
    )
    op.drop_constraint(
        "lesson_occurrence_participant_status_check",
        "lesson_occurrence_participant",
        type_="check",
    )
    op.create_check_constraint(
        "lesson_occurrence_participant_status_check",
        "lesson_occurrence_participant",
        _status_check(_PARTICIPANT_STATUSES_OLD),
    )

    op.drop_index("ix_charge_adjustment_lookup", table_name="charge_adjustment")
    op.drop_index("uq_charge_adjustment_carry", table_name="charge_adjustment")
    op.drop_table("charge_adjustment")

    op.drop_index("ix_monthly_charge_period_status", table_name="student_monthly_charge")
    op.drop_table("student_monthly_charge")

    op.drop_table("student_price_override")

    op.drop_index("ix_student_break_student", table_name="student_break")
    op.drop_table("student_break")
