"""tsk-756: у слота появляется дата НАЧАЛА действия, у месяца — счётчик несостоявшихся.

**Почему колонка `active_from`.** У `lesson_slot` была только дата конца
(`active_until`, tsk-679) и выключатель `is_active`. Даты начала не было вовсе,
поэтому слот, заведённый 31 августа под осеннее расписание, при расчёте августа
считался действовавшим с 1 августа — задним числом. На проде 01.09.2026 это
выставило четверым новичкам по 611 ₽ за занятие, которого не было, и троим
ученикам цену августа по СЕНТЯБРЬСКОЙ частоте занятий (tsk-756). Смена
расписания переписывала прошлое каждый раз, когда её применяли.

`NULL` — «действовал всегда», прежнее поведение. Бэкфилл ставит существующим
слотам день их создания по Москве: слот не мог работать раньше, чем появился, и
для сеток 26.07 и 31.08 это ровно та граница, которой не хватало.

**Почему `missing_lessons` отдельной колонкой, а не вычетом из `expected_lessons`.**
Та же причина, что у `not_started_lessons` (tsk-630): знаменатель доли остаётся
месячным, иначе сумма перестаёт объясняться. Экран начислений должен показывать
и «занятий в месяце по сетке», и сколько из них не состоялось, — иначе «611 ₽ у
человека без занятий» снова будет видно только после письма.

Rollback: `alembic downgrade tsk744_payment_block_hold`. Обе колонки снимаются;
откат схемы делается вместе с откатом кода — расчёт месяца их читает.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk756_slot_active_from"
down_revision: Union[str, None] = "tsk744_payment_block_hold"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "lesson_slot",
        sa.Column(
            "active_from",
            sa.Date(),
            nullable=True,
            comment=(
                "Первый день действия слота включительно; NULL — действовал "
                "всегда. Парная к active_until (tsk-756)"
            ),
        ),
    )
    # Бэкфилл по дню создания слота в Москве. Школа живёт по московскому времени
    # (`project_lms_school_time_is_moscow`), а `created_at` хранится в UTC:
    # слот, заведённый 31.08 в 21:13 МСК, в UTC уже 31.08 18:13 — но вечерний
    # слот, заведённый после 03:00 МСК, без приведения уехал бы на день назад.
    op.execute(
        """
        UPDATE lesson_slot
           SET active_from = (created_at AT TIME ZONE 'Europe/Moscow')::date
         WHERE active_from IS NULL
        """
    )

    # Снимок итога закончившегося месяца — опора стража сдвига (tsk-756).
    # Закрытие месяца делают руками и не сразу: 01.09.2026 август был ещё
    # открыт, поэтому «закрытый месяц не трогаем» его не защитило. Снимок
    # ставится сам, как только месяц кончился, и сравнение с ним показывает,
    # что сумма прошлого уехала, — сегодня это заметил человек, а не система.
    op.add_column(
        "student_monthly_charge",
        sa.Column(
            "frozen_total_minor",
            sa.Integer(),
            nullable=True,
            comment=(
                "Итог месяца на момент его окончания. Ставится автоматически "
                "после конца месяца и обновляется при ЯВНОЙ правке человека; "
                "расхождение с текущим итогом = сумма прошлого сдвинулась "
                "сама (tsk-756)"
            ),
        ),
    )
    op.add_column(
        "student_monthly_charge",
        sa.Column(
            "frozen_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Когда зафиксирован frozen_total_minor (tsk-756)",
        ),
    )

    op.add_column(
        "student_monthly_charge",
        sa.Column(
            "missing_lessons",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment=(
                "Занятий, которые расписание месяца предполагало, но которых в "
                "прошедшие дни не состоялось: за них не берут денег. Вычитается "
                "вместе с break_lessons и not_started_lessons; знаменатель доли "
                "остаётся expected_lessons (tsk-756)"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("student_monthly_charge", "missing_lessons")
    op.drop_column("student_monthly_charge", "frozen_at")
    op.drop_column("student_monthly_charge", "frozen_total_minor")
    op.drop_column("lesson_slot", "active_from")
