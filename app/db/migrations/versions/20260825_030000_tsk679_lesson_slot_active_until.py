"""tsk-679: слот действует по дату, а не «вечно или выключен».

До этой колонки у `lesson_slot` был только выключатель `is_active`: слот либо
идёт бесконечно, либо гасится насовсем. Выразить «отработать до 31 августа, а
с 1 сентября исчезнуть» было нечем — а именно это и происходит при смене
расписания на осеннее (tsk-674): старая сетка доживает август, новая начинается
с сентября.

Почему дата в данных, а не «погасим руками первого числа»: генератор занятий
пишет календарь на 14 дней вперёд, поэтому сентябрьские занятия по старому
расписанию появляются в базе уже в августе — ученик их видит и записывается.
Ручное гашение первого сентября опаздывает на две недели.

`NULL` — бессрочно, прежнее поведение всех существующих слотов. Дата
включительная: `active_until = 2026-08-31` означает, что занятие 31 августа
ещё есть, а 1 сентября уже нет.

Rollback: `alembic downgrade tsk673_alumni_course_work` — колонка снимается,
все слоты снова становятся бессрочными. Откат схемы делается вместе с откатом
кода: генератор занятий и расчёт месяца читают эту колонку, и без неё запрос
упадёт.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk679_lesson_slot_active_until"
down_revision: Union[str, None] = "tsk673_alumni_course_work"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "lesson_slot",
        sa.Column(
            "active_until",
            sa.Date(),
            nullable=True,
            comment=(
                "Последний день действия слота включительно; NULL — бессрочно "
                "(tsk-679)"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("lesson_slot", "active_until")
