"""tsk-610: признак «денег не берут осознанно» у тарифа.

Страж «ходит, но не выставлен» (tsk-596) сообщал про ученика на тарифе `test`
каждый день — законно: оператор решил денег с него не брать. В списке из двух
строк одна была всегда ложной, и уведомление три дня подряд провисело
непрочитанным вместе с настоящим случаем (Грабовский на `demo` с занятиями).
Предупреждение, которое не умеет молчать, перестают открывать.

Почему признак в данных, а не набором кодов в коде: следующий «денег не берём»
тариф не должен требовать релиза. `demo` намеренно НЕ помечается — ученик на
демо с реальными занятиями и есть та дыра, ради которой страж написан.
`alumni` тоже нет: выпускник с активным расписанием — сигнал, а не норма.

Rollback: `alembic downgrade tsk588_timezone_source` — колонка снимается,
детектор возвращается к прежнему поведению (лишний шум, деньги не затронуты).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk610_billing_exempt"
down_revision: Union[str, None] = "tsk588_timezone_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscription_plan",
        sa.Column(
            "billing_exempt",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment=(
                "Денег не берут ОСОЗНАННО: страж «ходит, но не выставлен» молчит "
                "про таких (tsk-610). Не то же, что pricing_group_id IS NULL — "
                "у demo группы тоже нет, но ученик на нём как раз аномалия"
            ),
        ),
    )
    op.execute("UPDATE subscription_plan SET billing_exempt = true WHERE code = 'test'")


def downgrade() -> None:
    op.drop_column("subscription_plan", "billing_exempt")
