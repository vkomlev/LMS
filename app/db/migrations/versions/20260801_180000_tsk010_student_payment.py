"""tsk-010: приём оплаты — платёж как ссылка на начисление, а не второй расчёт.

Одна таблица `student_payment`. Она НЕ считает, сколько должен ученик: сумма
месяца живёт в `student_monthly_charge` (tsk-511/512/513). Здесь только факт
денег — кто, за какой месяц, сколько и чем подтверждено.

Почему связь составным ключом (student_id, group_id, period), а не по `charge.id`:
это ровно тот ключ, которым начисление уникально, и он переживает пересоздание
строки месяца. `ON DELETE RESTRICT` — намеренно: пересчёт умеет удалять открытую
строку месяца, когда считать стало не из чего, и молча унёс бы вместе с ней
принятые деньги. Теперь такая строка не удалится, пока по ней есть платёж
(парная правка в `charge_service.recalculate_student_group`).

Оплаченность нигде не хранится полем — она выводится суммой подтверждённых
платежей против итога начисления. Иначе частичная оплата и правка суммы месяца
разъезжаются между двумя источниками правды.

Rollback: `alembic downgrade tsk511_charges_breaks` — сносит таблицу целиком.
ВНИМАНИЕ: вместе с ней пропадает вся история принятых платежей (кто и когда
подтвердил чек), восстановить будет неоткуда. Перед откатом на проде — снять
дамп `student_payment` и сохранить файлы чеков из `PAYMENT_RECEIPTS_UPLOAD_DIR`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "tsk010_student_payment"
down_revision = "tsk511_charges_breaks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "student_payment",
        sa.Column("id", sa.Integer, primary_key=True),
        # Тройка student_id+group_id+period — это внешний ключ на начисление
        # целиком (см. ForeignKeyConstraint ниже), поэтому отдельных FK на
        # users/pricing_group здесь нет: они уже гарантированы начислением.
        sa.Column("student_id", sa.Integer, nullable=False),
        sa.Column("group_id", sa.Integer, nullable=False),
        sa.Column("period", sa.Date, nullable=False),
        sa.Column("amount_minor", sa.Integer, nullable=False),
        sa.Column("method", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        # Ручной способ: файл чека лежит на диске, в базе — только его имя.
        sa.Column("receipt_file", sa.Text, nullable=True),
        # Исходное имя файла — показать человеку то, что он сам загрузил.
        sa.Column("receipt_name", sa.Text, nullable=True),
        sa.Column("payer_note", sa.Text, nullable=True),
        # Дата, когда деньги реально ушли. Отдельно от created_at: чек могут
        # загрузить позже, а сверять с «Мой налог» нужно по дате платежа.
        sa.Column("paid_on", sa.Date, nullable=True),
        sa.Column(
            "submitted_by",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Авто-способ (шлюз). Пусто, пока способ не подключён.
        sa.Column("gateway", sa.Text, nullable=True),
        sa.Column("gateway_payment_id", sa.Text, nullable=True),
        sa.Column(
            "reviewed_by",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["student_id", "group_id", "period"],
            [
                "student_monthly_charge.student_id",
                "student_monthly_charge.group_id",
                "student_monthly_charge.period",
            ],
            name="fk_student_payment_charge",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.CheckConstraint("amount_minor > 0", name="ck_student_payment_amount_positive"),
        sa.CheckConstraint("method IN ('manual', 'gateway')", name="ck_student_payment_method"),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'rejected')", name="ck_student_payment_status"
        ),
        sa.CheckConstraint(
            "date_trunc('month', period::timestamp)::date = period",
            name="ck_student_payment_period_is_month_start",
        ),
        # Решение по платежу всегда оставляет след: без этого «подтверждено»
        # становится состоянием без автора и времени.
        sa.CheckConstraint(
            "(status = 'pending') = (reviewed_at IS NULL)",
            name="ck_student_payment_reviewed_has_timestamp",
        ),
        # Платёж из шлюза обязан помнить, из какого именно и по какой транзакции —
        # иначе его нечем сверить с кабинетом шлюза при разборе спорного случая.
        sa.CheckConstraint(
            "method <> 'gateway' OR (gateway IS NOT NULL AND gateway_payment_id IS NOT NULL)",
            name="ck_student_payment_gateway_ids",
        ),
        # Шлюз и номер транзакции — всегда парой. Иначе номер без шлюза
        # проскочит мимо уникального индекса ниже (NULL там различимы) и
        # защита от повторной доставки уведомления окажется дырявой.
        sa.CheckConstraint(
            "(gateway IS NULL) = (gateway_payment_id IS NULL)",
            name="ck_student_payment_gateway_pair",
        ),
    )
    # Двойное нажатие «Отправить чек» не должно превращаться в двойные деньги.
    # Ограничиваем только ОЖИДАЮЩИЕ решения: повтор той же суммы за тот же день
    # и месяц — это почти всегда потерянный ответ и второе нажатие. После
    # решения по первому платежу такая же сумма законна (доплата равными
    # частями), поэтому подтверждённые и отклонённые под ограничение не идут.
    # COALESCE, а не голый `paid_on`: два NULL в уникальном индексе считаются
    # разными, и платёж без указанной даты обошёл бы ограничение вдвоём.
    op.create_index(
        "uq_student_payment_pending_duplicate",
        "student_payment",
        [
            "student_id",
            "group_id",
            "period",
            "amount_minor",
            sa.text("COALESCE(paid_on, DATE '1970-01-01')"),
        ],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    # Повторная доставка уведомления от шлюза — обычное дело, а не сбой.
    # Уникальность транзакции на уровне БД: вторая вставка того же платежа
    # отбивается здесь, даже если проверка в коде проспала гонку.
    op.create_index(
        "uq_student_payment_gateway_txn",
        "student_payment",
        ["gateway", "gateway_payment_id"],
        unique=True,
        postgresql_where=sa.text("gateway_payment_id IS NOT NULL"),
    )
    # Сколько уже оплачено по конкретному месяцу — самый частый запрос.
    op.create_index(
        "ix_student_payment_charge",
        "student_payment",
        ["student_id", "group_id", "period"],
    )
    # Очередь маркетолога: сначала то, что ждёт решения.
    op.create_index(
        "ix_student_payment_status",
        "student_payment",
        ["status", "created_at"],
    )
    # Выгрузка за период для сверки с чеками «Мой налог».
    op.create_index(
        "ix_student_payment_paid_on",
        "student_payment",
        ["paid_on"],
        postgresql_where=sa.text("status = 'confirmed'"),
    )


def downgrade() -> None:
    op.drop_index("uq_student_payment_pending_duplicate", table_name="student_payment")
    op.drop_index("ix_student_payment_paid_on", table_name="student_payment")
    op.drop_index("ix_student_payment_status", table_name="student_payment")
    op.drop_index("ix_student_payment_charge", table_name="student_payment")
    op.drop_index("uq_student_payment_gateway_txn", table_name="student_payment")
    op.drop_table("student_payment")
