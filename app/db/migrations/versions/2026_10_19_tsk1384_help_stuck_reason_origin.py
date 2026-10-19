"""tsk-1384: причина «не знаю, с чего начать» и устная заявка помощи.

Доработка П4 (tsk-993, «сначала попробуй сам»):

- `help_requests.stuck_reason` — на каком основании ученик открыл помощь без
  своей сдачи: `note` — заметка «где застрял» (≥20 символов), `no_start` —
  кнопка «Не понимаю, с чего начать». Пишется по факту обращения, независимо
  от сдачи и гейта; NULL — не описывал и не нажимал. Уже записанные заметки
  получают `note`.
- `help_requests.origin` — откуда заявка: `oral` — преподаватель записал
  устную просьбу ученика (на занятии), `student` или NULL — ученик сам (все
  прежние заявки остаются NULL: колонка без значения по умолчанию).

Обе колонки с CHECK на закрытый набор значений.

Rollback: `alembic downgrade tsk999_help_reflection` — колонки снимаются,
причины теряются; устные заявки остаются обычными заявками `manual_help`
(без признака «устно»).

Revision ID: tsk1384_help_stuck_reason
Revises: tsk999_help_reflection
Create Date: 2026-10-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk1384_help_stuck_reason"
down_revision: Union[str, None] = "tsk999_help_reflection"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавить `stuck_reason` и `origin` в заявки помощи."""
    op.add_column(
        "help_requests",
        sa.Column(
            "stuck_reason",
            sa.String(16),
            nullable=True,
            comment=(
                "Основание помощи без сдачи (tsk-1384): note — заметка «где застрял», "
                "no_start — «не понимаю, с чего начать». NULL — не описывал и не нажимал."
            ),
        ),
    )
    op.create_check_constraint(
        "help_requests_stuck_reason_check",
        "help_requests",
        "stuck_reason IN ('no_start', 'note')",
    )
    op.add_column(
        "help_requests",
        sa.Column(
            "origin",
            sa.String(16),
            nullable=True,
            comment=(
                "Откуда заявка (tsk-1384): oral — устная просьба, записанная "
                "преподавателем; student или NULL — ученик сам."
            ),
        ),
    )
    op.create_check_constraint(
        "help_requests_origin_check",
        "help_requests",
        "origin IN ('student', 'oral')",
    )
    # Заметки, оставленные до tsk-1384, — то же основание `note`.
    op.execute(
        "UPDATE help_requests SET stuck_reason = 'note' "
        "WHERE stuck_note IS NOT NULL AND stuck_reason IS NULL"
    )


def downgrade() -> None:
    """Снять `origin` и `stuck_reason`."""
    op.drop_constraint("help_requests_origin_check", "help_requests", type_="check")
    op.drop_column("help_requests", "origin")
    op.drop_constraint("help_requests_stuck_reason_check", "help_requests", type_="check")
    op.drop_column("help_requests", "stuck_reason")
