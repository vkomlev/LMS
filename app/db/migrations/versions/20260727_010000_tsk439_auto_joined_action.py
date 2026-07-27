"""tsk-439: Календарь LMS — новое системное действие явки 'auto_joined'.

Ученик, у которого прямо сейчас идёт занятие (участие в статусе 'scheduled',
время в пределах [scheduled_at, scheduled_at+duration)), совершает реальное
учебное действие (сдача ответа на задание / завершение материала) — система
автоматически подтверждает явку без явного клика "Я на занятии" (решение
оператора: реальное учебное действие = явка).

`actor_user_id` = сам ученик (в отличие от 'auto_no_show', где NULL — там
действия со стороны ученика как раз не было).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "tsk439_auto_joined_action"
down_revision: Union[str, None] = "tsk435_lesson_calendar_groups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_CHECK = (
    "action IN ('joined', 'declined', 'manual_present', 'manual_absent', "
    "'auto_no_show')"
)
_NEW_CHECK = (
    "action IN ('joined', 'declined', 'manual_present', 'manual_absent', "
    "'auto_no_show', 'auto_joined')"
)


def upgrade() -> None:
    op.drop_constraint(
        "attendance_event_action_check", "attendance_event", type_="check",
    )
    op.create_check_constraint(
        "attendance_event_action_check", "attendance_event", _NEW_CHECK,
    )


def downgrade() -> None:
    op.drop_constraint(
        "attendance_event_action_check", "attendance_event", type_="check",
    )
    op.create_check_constraint(
        "attendance_event_action_check", "attendance_event", _OLD_CHECK,
    )
