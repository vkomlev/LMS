"""Даты действия у привязки ученика к слоту (tsk-1442).

Revision ID: tsk1442_slot_student_dates
Revises: tsk1407_signup_attribution
Create Date: 2026-10-28

Смена расписания выключала старую привязку целиком, и расчёт месяца строился
по новому слоту с его первого дня — прошедшие дни старого слота становились
«пропавшими» занятиями (ученик 4623: 600 ₽ вместо 3000 за октябрь).

* ``active_from`` / ``active_until`` — даты привязки по МСК, включительно.
  Существующим строкам — NULL: для действующих это «как у слота», для снятых
  без даты — «в расчёт не входит», ровно как было. Суммы остальных учеников
  поэтому не меняются.
* Уникальность пары «слот + ученик» — только среди действующих: закрытый
  период остаётся отдельной строкой истории.
* Данные: перевод 4623 со слота 33 на слот 36 от 28.10 — старая привязка
  действует по 27.10, новая с 28.10. Условие по трём полям: на базе, где этих
  строк нет (dev, тесты), правка ничего не трогает.

Откат: снять колонки и вернуть полную уникальность. Если после выката у пары
появилась вторая строка, откат упадёт на уникальности — сначала удалить
закрытые строки-дубли.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk1442_slot_student_dates"
down_revision: Union[str, None] = "tsk1407_signup_attribution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавить даты привязки, сузить уникальность до действующих, починить 4623."""
    op.add_column(
        "lesson_slot_student",
        sa.Column(
            "active_from", sa.Date(), nullable=True,
            comment="Первый день привязки (МСК); NULL — с начала, как у слота",
        ),
    )
    op.add_column(
        "lesson_slot_student",
        sa.Column(
            "active_until", sa.Date(), nullable=True,
            comment="Последний день привязки (МСК); NULL — открыта",
        ),
    )
    op.drop_constraint(
        "uq_lesson_slot_student_slot_student", "lesson_slot_student", type_="unique"
    )
    op.create_index(
        "uq_lesson_slot_student_active",
        "lesson_slot_student",
        ["slot_id", "student_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.execute(
        "UPDATE lesson_slot_student SET active_until = DATE '2026-10-27' "
        " WHERE id = 231 AND student_id = 4623 AND slot_id = 33 AND NOT is_active"
    )
    op.execute(
        "UPDATE lesson_slot_student SET active_from = DATE '2026-10-28' "
        " WHERE id = 332 AND student_id = 4623 AND slot_id = 36 AND is_active"
    )


def downgrade() -> None:
    """Вернуть полную уникальность пары и снять даты."""
    op.drop_index("uq_lesson_slot_student_active", table_name="lesson_slot_student")
    op.create_unique_constraint(
        "uq_lesson_slot_student_slot_student",
        "lesson_slot_student",
        ["slot_id", "student_id"],
    )
    op.drop_column("lesson_slot_student", "active_until")
    op.drop_column("lesson_slot_student", "active_from")
