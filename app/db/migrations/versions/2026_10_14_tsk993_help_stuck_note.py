"""tsk-993 (П4): «где застрял» у заявки помощи и у разговора с наставником.

Помощь по заданию при включённом `HELP_REQUIRES_ATTEMPT` открывается после
своей сдачи ИЛИ описания «где застрял» (не короче 20 символов). Описание
сохраняется:

- `help_requests.stuck_note` — преподаватель видит его в карточке заявки;
- `ai_tutor_session.stuck_note` — уходит в контекст первой реплики наставника
  (системная инструкция пересобирается на каждом ходе, поэтому заметка нужна
  в строке сессии, а не только в запросе открытия).

Обе колонки необязательные, без значения по умолчанию — старые строки и
старые клиенты не затронуты.

Rollback: `alembic downgrade tsk1220_task_hint_drafts` — колонки снимаются,
заметки теряются (сами заявки и разговоры остаются).

Revision ID: tsk993_help_stuck_note
Revises: tsk1220_task_hint_drafts
Create Date: 2026-10-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk993_help_stuck_note"
down_revision: Union[str, None] = "tsk1220_task_hint_drafts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавить `stuck_note TEXT NULL` в заявки помощи и сессии наставника."""
    op.add_column(
        "help_requests",
        sa.Column(
            "stuck_note",
            sa.Text(),
            nullable=True,
            comment="Где ученик застрял, своими словами (tsk-993). NULL — не описывал.",
        ),
    )
    op.add_column(
        "ai_tutor_session",
        sa.Column(
            "stuck_note",
            sa.Text(),
            nullable=True,
            comment="Где ученик застрял, со слов при открытии разговора (tsk-993).",
        ),
    )


def downgrade() -> None:
    """Снять колонки `stuck_note`."""
    op.drop_column("ai_tutor_session", "stuck_note")
    op.drop_column("help_requests", "stuck_note")
