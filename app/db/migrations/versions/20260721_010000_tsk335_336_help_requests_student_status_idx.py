"""Индекс (student_id, status, task_id) на help_requests (tsk-335/tsk-336).

Под новый батч-запрос открытых заявок помощи по дереву прогресса ученика
(`manual_progress_service.get_student_progress`, значок заявки + подсветка
"требует внимания"): `WHERE student_id = :sid AND status = 'open' AND
task_id = ANY(:task_ids)` на каждый просмотр карточки учителем.

Не блокирует функциональность без себя — существующий
`idx_help_requests_student_created(student_id, created_at DESC)` уже покрывает
`student_id`, а объём заявок на одного ученика мал. Добавлен как дешёвая
профилактика, а не как фикс замеренной проблемы.

Revision ID: tsk335_336_hr_student_status_idx
Revises: tsk297_manual_progress_source
Create Date: 2026-07-21 01:00:00
"""
from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "tsk335_336_hr_student_status_idx"
down_revision: Union[str, None] = "tsk297_manual_progress_source"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

_INDEX_NAME = "idx_help_requests_student_status_task"


def upgrade() -> None:
    op.create_index(
        _INDEX_NAME,
        "help_requests",
        ["student_id", "status", "task_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="help_requests")
