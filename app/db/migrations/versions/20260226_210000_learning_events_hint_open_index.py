"""Индексы для подсчёта hint_open (этап 3.6, performance)

Revision ID: hint_open_index_stage36
Revises: attempt_cancel_stage35
Create Date: 2026-02-26 21:00:00

- Частичные индексы по learning_events для event_type='hint_open':
  - по (payload->>'task_id')::int для фильтра по задаче/курсу;
  - по student_id для фильтра по пользователю.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "hint_open_index_stage36"
down_revision: Union[str, None] = "attempt_cancel_stage35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX idx_learning_events_hint_open_task
        ON learning_events (((payload->>'task_id')::int))
        WHERE event_type = 'hint_open'
    """)
    op.execute("""
        CREATE INDEX idx_learning_events_hint_open_student
        ON learning_events (student_id)
        WHERE event_type = 'hint_open'
    """)


def downgrade() -> None:
    op.drop_index("idx_learning_events_hint_open_student", table_name="learning_events")
    op.drop_index("idx_learning_events_hint_open_task", table_name="learning_events")
