"""tsk-630: занятия месяца ДО прихода ученика — отдельным счётчиком.

Месяц, в который ученик пришёл среди месяца, до сих пор считался полным:
`expected_lessons` берётся по постоянному расписанию на ВЕСЬ месяц, независимо
от того, что расписание завели 19 числа. Умеров пришёл 19.08, занятий у него в
августе три, а начислено было 6 000 рублей — цена целого месяца.

Механизм доли в системе уже был — перерывы (`break_lessons`). Не хватало
второго вычета: занятий, которые пришлись на дни ДО постановки ученика в
расписание. Он и добавляется этой колонкой.

Почему отдельная колонка, а не «уменьшить expected_lessons»: знаменатель доли
должен остаться месячным. Ширинов платит 5 500 × 5/9, и на экране начислений
обязаны быть видны обе цифры — девять занятий в месяце и четыре из них до его
прихода. Спрятав это в expected, мы получили бы на экране «занятий 5» и сумму
3 055,55 рублей, из которой уже не выводится, почему она такая.

Backfill не нужен: значение пересчитывается вместе с суммой при первом же
пересчёте открытого месяца, а закрытые месяцы намеренно не переписываются.

Rollback: `alembic downgrade tsk591_lesson_idle` — колонка снимается, расчёт
возвращается к «неполный месяц считается полным» (суммы вырастут обратно).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk630_not_started_lessons"
down_revision: Union[str, None] = "tsk591_lesson_idle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "student_monthly_charge",
        sa.Column(
            "not_started_lessons",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment=(
                "Занятий месяца, пришедшихся на дни ДО постановки ученика в "
                "расписание (tsk-630). Вычитается из оплачиваемых так же, как "
                "break_lessons; знаменатель доли остаётся expected_lessons"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("student_monthly_charge", "not_started_lessons")
