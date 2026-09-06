"""tsk-804: у месяца появляется счётчик занятий ПОСЛЕ ухода ученика.

**Почему колонка, а не вычет из `expected_lessons`.** Та же причина, что у
`not_started_lessons` (tsk-630) и `missing_lessons` (tsk-756): знаменатель доли
остаётся месячным, иначе сумма перестаёт объясняться. Экран начислений и
предпросмотр рассылки должны показывать и «занятий в месяце по сетке», и
сколько из них пришлось на дни, когда человек уже не учился.

**Что чинит.** Начисление считается на весь месяц вперёд по сетке расписания, а
у прихода ученика вычет был (`not_started`), у ухода — нет. Поэтому выпуск
06.09.2026 закрыл Гребневой Полине сентябрь целиком: из девяти занятий по сетке
к моменту ухода прошло одно, а долг выставлен за все девять — 5 500 ₽. У второй
ученицы, ушедшей 01.09, то же самое дало 6 000 ₽ за месяц, в котором она не
училась ни дня. Раньше это закрывали руками, оформляя перерыв «окончание
обучения», — автоматический выпуск (tsk-673) этот шаг не унаследовал.

Rollback: `alembic downgrade tsk798_core_priority`. Колонка снимается; откат
схемы делается вместе с откатом кода — расчёт месяца её читает.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk804_after_leave_lessons"
down_revision: Union[str, None] = "tsk798_core_priority"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "student_monthly_charge",
        sa.Column(
            "after_leave_lessons",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment=(
                "Занятий, которые расписание месяца предполагало на дни ПОСЛЕ "
                "ухода ученика из школы: за них не берут денег. Зеркало "
                "not_started_lessons, вычитается вместе с ним, break_lessons и "
                "missing_lessons; знаменатель доли остаётся expected_lessons "
                "(tsk-804)"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("student_monthly_charge", "after_leave_lessons")
