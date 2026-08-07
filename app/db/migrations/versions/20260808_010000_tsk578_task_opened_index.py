"""Индексы для чтения task_opened (tsk-578, телеметрия открытия задания)

Revision ID: tsk578_task_opened_idx
Revises: tsk572_gap_signals
Create Date: 2026-08-08 01:00:00

Новой таблицы/колонки не заводится — событие пишется в уже существующую
общую `learning_events` (event_type='task_opened'), по прецеденту
`hint_open_index_stage36`. Composite-индекс отдельный от прецедента: pace-CTE
в `topic_mastery_service` ищет ближайшее ПЕРЕД сдачей событие конкретной пары
(student_id, task_id) — нужен порядок по created_at внутри пары, простого
индекса по task_id/student_id по отдельности для этого недостаточно.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "tsk578_task_opened_idx"
down_revision: Union[str, None] = "tsk572_gap_signals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Основной путь чтения: LATERAL "последнее task_opened для (student_id,
    # task_id) с created_at <= received_at" — student_id и task_id первыми
    # колонками, created_at третьей для сортировки/MAX без доп. сортировки.
    op.execute("""
        CREATE INDEX idx_learning_events_task_opened_lookup
        ON learning_events (student_id, ((payload->>'task_id')::int), created_at)
        WHERE event_type = 'task_opened'
    """)


def downgrade() -> None:
    op.drop_index("idx_learning_events_task_opened_lookup", table_name="learning_events")
