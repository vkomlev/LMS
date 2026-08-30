"""tsk-744: отсрочка блокировки за неоплату конкретному ученику.

Зачем. Блокировка за неоплату выводится из данных и наступает сама (tsk-010).
Это её достоинство — она не может «не сработать», — и одновременно причина, по
которой у оператора не было никакого рычага: договорился с семьёй подождать до
среды, а закрыть занятия было нечем, кроме сдвига срока СРАЗУ ВСЕЙ школе или
проведения платежа, которого не было.

Почему отдельная таблица, а не колонка в `users`. Отсрочка — событие с автором,
причиной и сроком, а не свойство человека: важно, кто и почему разрешил, и
таких разрешений за год у одного ученика может быть несколько. Колонка хранила
бы только последнее и молча теряла историю — тот же довод, по которому
`student_break` и `student_price_override` живут своими таблицами.

Почему `until` обязателен. Решение оператора 31.08: бессрочной отсрочки не
бывает. Дата истекает сама, поэтому забыть про ученика невозможно; нужно
дольше — ставится новая отсрочка, и в истории видно, сколько раз откладывали.

Причина (`reason`) тоже обязательна и непустая: строка «отложено» без объяснения
через месяц не отличается от ошибки.

На поведение миграция не влияет: таблица после накатки пуста, а пустая таблица
означает «отсрочек нет» — блокировка работает ровно как вчера.

Rollback: `alembic downgrade tsk718_lead_external_ref`. Снимаются только
отсрочки; долги, платежи и блокировка не затрагиваются.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk744_payment_block_hold"
down_revision: Union[str, None] = "tsk718_lead_external_ref"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_block_hold",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column(
            "until",
            sa.Date(),
            nullable=False,
            comment="Последний день отсрочки включительно; с завтра — как обычно",
        ),
        sa.Column(
            "reason",
            sa.Text(),
            nullable=False,
            comment="Почему отложили — для того, кто увидит эту строку через месяц",
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "cancelled_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Снята досрочно. Строка не удаляется — история остаётся",
        ),
        sa.Column("cancelled_by", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="payment_block_hold_pkey"),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="payment_block_hold_student_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            ondelete="SET NULL",
            name="payment_block_hold_created_by_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by"],
            ["users.id"],
            ondelete="SET NULL",
            name="payment_block_hold_cancelled_by_fkey",
        ),
        # «Непустая строка» в PG — это `~ '\S'`, а не `length(btrim(x)) > 0`:
        # btrim без второго аргумента срезает только пробелы и пропускает
        # табуляцию с переводом строки (урок tsk-303).
        sa.CheckConstraint("reason ~ '\\S'", name="ck_payment_block_hold_reason"),
        comment="Отсрочки блокировки за неоплату по ученику (tsk-744)",
    )
    # Читается на каждом гейте учебного контента у должника — только по
    # действующим строкам, поэтому индекс частичный.
    op.create_index(
        "ix_payment_block_hold_active",
        "payment_block_hold",
        ["student_id", "until"],
        postgresql_where=sa.text("cancelled_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_payment_block_hold_active", table_name="payment_block_hold")
    op.drop_table("payment_block_hold")
