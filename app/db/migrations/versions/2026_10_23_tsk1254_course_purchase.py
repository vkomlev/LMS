"""tsk-1254: разовая покупка курса и срок доступа к курсу.

До сих пор все экземпляры платформы работали на помесячной оплате: зачисление
не истекало, срок ограничивала только блокировка по долгу. Решение оператора
23.10.2026: нужна разовая оплата курса целиком (доли — через банк, способами
ЮKassa: банк платит школе всю сумму сразу, долг ученика перед банком) и срок
доступа, после которого курс остаётся только для чтения. Механизм общий для
любых курсов, первым идёт пилот «Дипломат».

Что меняется — только добавляется, ни одна существующая строка не трогается:

* `pricing_tariff.period` получает закрытый список `month | once`. Колонка была
  свободной строкой, и расчёт месяца её не читал: тариф с другим периодом молча
  начислялся бы помесячно. На 23.10 у всех 14 тарифов прода `month`, CHECK
  встаёт без правки данных. Плюс `access_months` (срок доступа разовой покупки)
  и `installments_allowed` (разрешены ли доли через банк).
* `course_purchase` — покупка курса: ученик, курс, тарифная группа и цена
  СНИМКОМ на момент покупки (правка тарифа потом не переписывает чужую сделку),
  способ, статус, срок доступа.
* `student_payment` — назначение `course` и ссылка на покупку. Правило tsk-615
  «не-месячный платёж без группы и месяца» сохраняется, поэтому месячные
  подсчёты платёж за курс не видят и чужой месячный долг он не гасит.
* `user_courses.access_until` — последний момент доступа. NULL = бессрочно: все
  текущие зачисления остаются как были.

Rollback: `alembic downgrade tsk997_learning_ach`. Откат осознанно
падает, если покупки или платежи за курс уже есть: удалять принятые деньги
откатом схемы нельзя — сначала выгрузить и решить, куда их деть.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk1254_course_purchase"
down_revision: Union[str, None] = "tsk997_learning_ach"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- тариф: разовый период и срок доступа
    op.create_check_constraint(
        "ck_pricing_tariff_period", "pricing_tariff", "period IN ('month', 'once')"
    )
    op.add_column(
        "pricing_tariff",
        sa.Column(
            "access_months",
            sa.Integer(),
            nullable=True,
            comment="Срок доступа разовой покупки в месяцах (tsk-1254); у месячного тарифа пусто",
        ),
    )
    op.add_column(
        "pricing_tariff",
        sa.Column(
            "installments_allowed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Разрешена ли оплата долями через банк (tsk-1254)",
        ),
    )
    # Разовый тариф без срока продать нельзя: доступ не знал бы, когда кончиться.
    op.create_check_constraint(
        "ck_pricing_tariff_once_access",
        "pricing_tariff",
        "(period = 'once') = (access_months IS NOT NULL) AND "
        "(access_months IS NULL OR access_months BETWEEN 1 AND 120)",
    )

    # Индекс tsk-996 «один действующий вариант без оси на группу» писался под
    # помесячные тарифы. С разовым он запретил бы ровно нужное: одному курсу —
    # и «помесячно», и «курс целиком». Сужаем его до месяца, а разовому даём
    # свой такой же: два разовых в группе задвоили бы цену покупки.
    op.drop_index("uq_pricing_tariff_single_active", table_name="pricing_tariff")
    op.create_index(
        "uq_pricing_tariff_single_active", "pricing_tariff", ["group_id"], unique=True,
        postgresql_where=sa.text("is_active AND match_kind IS NULL AND period = 'month'"),
    )
    op.create_index(
        "uq_pricing_tariff_single_active_once", "pricing_tariff", ["group_id"], unique=True,
        postgresql_where=sa.text("is_active AND period = 'once'"),
    )

    # --- покупка курса
    op.create_table(
        "course_purchase",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False, comment="Корневой курс покупки"),
        sa.Column(
            "group_id", sa.Integer(), nullable=False,
            comment="Тарифная группа на момент покупки — её месяц больше не начисляется",
        ),
        sa.Column("tariff_id", sa.Integer(), nullable=True),
        sa.Column("price_minor", sa.Integer(), nullable=False, comment="Цена снимком, копейки"),
        sa.Column("currency", sa.Text(), nullable=False, server_default=sa.text("'RUB'")),
        sa.Column("access_months", sa.Integer(), nullable=False),
        sa.Column(
            "pay_method", sa.Text(), nullable=False, server_default=sa.text("'full'"),
            comment="full — целиком; bank_installments — доли через банк (деньги школе сразу)",
        ),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default=sa.text("'pending'"),
            comment="pending — ждёт оплаты; paid — оплачена; cancelled; refunded — возврат вручную",
        ),
        sa.Column("access_from", sa.Date(), nullable=True, comment="Первый день доступа (МСК)"),
        sa.Column("access_until", sa.Date(), nullable=True, comment="Последний день доступа (МСК)"),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="RESTRICT",
                                name="course_purchase_student_id_fkey"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="RESTRICT",
                                name="course_purchase_course_id_fkey"),
        sa.ForeignKeyConstraint(["group_id"], ["pricing_group.id"], ondelete="RESTRICT",
                                name="course_purchase_group_id_fkey"),
        sa.ForeignKeyConstraint(["tariff_id"], ["pricing_tariff.id"], ondelete="SET NULL",
                                name="course_purchase_tariff_id_fkey"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL",
                                name="course_purchase_created_by_fkey"),
        sa.CheckConstraint("price_minor >= 0", name="ck_course_purchase_price"),
        sa.CheckConstraint("access_months BETWEEN 1 AND 120", name="ck_course_purchase_access_months"),
        sa.CheckConstraint("pay_method IN ('full', 'bank_installments')", name="ck_course_purchase_pay_method"),
        sa.CheckConstraint(
            "status IN ('pending', 'paid', 'cancelled', 'refunded')", name="ck_course_purchase_status"
        ),
        # Оплаченная покупка без срока — доступ, который никто не сможет ни
        # показать, ни закончить.
        sa.CheckConstraint(
            "status <> 'paid' OR (access_from IS NOT NULL AND access_until IS NOT NULL "
            "AND access_until >= access_from)",
            name="ck_course_purchase_paid_has_access",
        ),
        comment="Разовая покупка курса (tsk-1254)",
    )
    op.create_index(
        "ix_course_purchase_student_group_paid",
        "course_purchase",
        ["student_id", "group_id"],
        postgresql_where=sa.text("status = 'paid'"),
    )

    # --- платёж за курс
    op.add_column(
        "student_payment",
        sa.Column("course_purchase_id", sa.Integer(), nullable=True,
                  comment="Покупка курса, за которую платёж (tsk-1254)"),
    )
    op.create_foreign_key(
        "student_payment_course_purchase_id_fkey", "student_payment", "course_purchase",
        ["course_purchase_id"], ["id"], ondelete="RESTRICT",
    )
    op.drop_constraint("ck_student_payment_purpose", "student_payment")
    op.create_check_constraint(
        "ck_student_payment_purpose", "student_payment",
        "purpose IN ('monthly', 'ai_package', 'course')",
    )
    # Ссылка на покупку есть ровно у платежа за курс — не у месячного и не у пакета.
    op.create_check_constraint(
        "ck_student_payment_course_link", "student_payment",
        "(purpose = 'course') = (course_purchase_id IS NOT NULL)",
    )
    op.create_index(
        "ix_student_payment_course_purchase", "student_payment", ["course_purchase_id"],
        postgresql_where=sa.text("course_purchase_id IS NOT NULL"),
    )

    # --- срок доступа зачисления
    op.add_column(
        "user_courses",
        sa.Column("access_until", sa.Date(), nullable=True,
                  comment="Последний день доступа к курсу (МСК); NULL — бессрочно (tsk-1254)"),
    )


def downgrade() -> None:
    conn = op.get_bind()
    purchases, payments, terms = conn.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM course_purchase), "
            "       (SELECT count(*) FROM student_payment WHERE purpose = 'course'), "
            "       (SELECT count(*) FROM user_courses WHERE access_until IS NOT NULL) "
            "     + (SELECT count(*) FROM pricing_tariff WHERE period = 'once')"
        )
    ).one()
    if purchases or payments or terms:
        # Сроки доступа тоже данные: откат молча сделал бы их бессрочными.
        raise RuntimeError(
            f"Откат остановлен: покупок курса {purchases}, платежей за курс {payments}, "
            f"зачислений со сроком доступа и разовых тарифов {terms}. Откат схемы удалил бы принятые "
            "деньги и сроки. Выгрузите course_purchase, student_payment WHERE "
            "purpose = 'course' и user_courses WHERE access_until IS NOT NULL и решите, "
            "куда их деть."
        )
    op.drop_column("user_courses", "access_until")
    op.drop_index("ix_student_payment_course_purchase", table_name="student_payment")
    op.drop_constraint("ck_student_payment_course_link", "student_payment")
    op.drop_constraint("ck_student_payment_purpose", "student_payment")
    op.create_check_constraint(
        "ck_student_payment_purpose", "student_payment", "purpose IN ('monthly', 'ai_package')"
    )
    op.drop_constraint("student_payment_course_purchase_id_fkey", "student_payment", type_="foreignkey")
    op.drop_column("student_payment", "course_purchase_id")
    op.drop_index("ix_course_purchase_student_group_paid", table_name="course_purchase")
    op.drop_table("course_purchase")
    op.drop_index("uq_pricing_tariff_single_active_once", table_name="pricing_tariff")
    op.drop_index("uq_pricing_tariff_single_active", table_name="pricing_tariff")
    op.create_index(
        "uq_pricing_tariff_single_active", "pricing_tariff", ["group_id"], unique=True,
        postgresql_where=sa.text("is_active AND match_kind IS NULL"),
    )
    op.drop_constraint("ck_pricing_tariff_once_access", "pricing_tariff")
    op.drop_column("pricing_tariff", "installments_allowed")
    op.drop_column("pricing_tariff", "access_months")
    op.drop_constraint("ck_pricing_tariff_period", "pricing_tariff")
