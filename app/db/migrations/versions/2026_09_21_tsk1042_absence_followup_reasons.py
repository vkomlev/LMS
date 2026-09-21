"""tsk-1042: две новые причины пропуска — «отработка дома», «перенос в будущем».

Оператор 21.09: к пропуску нужны причины, которых не было в исходном наборе
(tsk-743) — ученик может быть уже решённым случаем: отработает дома
(`makeup`) или урок перенесён на будущее (`reschedule`). Это не то же самое,
что системный статус `rescheduled` участника занятия (перенос УЖЕ ЭТОГО
занятия, ставится автоматически) — здесь это отметка преподавателя про то,
что он решил делать с прошлым пропуском.

Rollback: `alembic downgrade tsk1022_manual_payment_reversed`. Если к тому
моменту есть строки с `reason IN ('makeup', 'reschedule')`, откат упрётся в
ограничение — перевести такие строки в `other` вручную до отката.

Revision ID: tsk1042_absence_followup_reasons
Revises: tsk1022_manual_payment_reversed
Create Date: 2026-09-21
"""
from typing import Sequence, Union

from alembic import op

revision: str = "tsk1042_absence_followup_reasons"
down_revision: Union[str, None] = "tsk1022_manual_payment_reversed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_lesson_absence_followup_reason", "lesson_absence_followup", type_="check",
    )
    op.create_check_constraint(
        "ck_lesson_absence_followup_reason",
        "lesson_absence_followup",
        "reason IS NULL OR reason IN ('illness', 'forgot', 'busy', 'no_answer', "
        "'other', 'makeup', 'reschedule')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_lesson_absence_followup_reason", "lesson_absence_followup", type_="check",
    )
    op.create_check_constraint(
        "ck_lesson_absence_followup_reason",
        "lesson_absence_followup",
        "reason IS NULL OR reason IN ('illness', 'forgot', 'busy', 'no_answer', 'other')",
    )
