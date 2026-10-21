"""tsk-1398: опрос «Вы будете на занятии?» во внешних каналах.

Раньше оператор вручную писал каждому не пришедшему ученику. Теперь проход
посещаемости через N минут после начала спрашивает сам (ВК, MAX, Telegram),
ученик отвечает кнопкой: «Опаздываю» или причина пропуска. Причина ложится в
`lesson_absence_followup` без преподавателя; не ответил до конца занятия —
`no_answer`.

`absence_poll` — одна строка на участие в занятии (`participant_id` уникален):
когда спросили, что ответил, когда ответ перенесён в отметку о пропуске
(`applied_at`). Данных не меняет.

Rollback: `alembic downgrade tsk1253_messenger_link` — таблица удаляется;
перенесённые причины остаются в `lesson_absence_followup`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "tsk1398_absence_poll"
down_revision: Union[str, None] = "tsk1253_messenger_link"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Создать таблицу опросов о пропуске."""
    op.create_table(
        "absence_poll",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "participant_id", sa.Integer,
            sa.ForeignKey("lesson_occurrence_participant.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "occurrence_id", sa.Integer,
            sa.ForeignKey("lesson_occurrence.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("student_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("answer", sa.Text, nullable=True),
        sa.Column("answer_channel", sa.Text, nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "answer IS NULL OR answer IN ('late', 'illness', 'busy', 'forgot', 'other')",
            name="ck_absence_poll_answer",
        ),
        sa.UniqueConstraint("participant_id", name="uq_absence_poll_participant"),
    )
    op.create_index("ix_absence_poll_student_open", "absence_poll", ["student_id", "asked_at"])


def downgrade() -> None:
    """Удалить таблицу опросов."""
    op.drop_index("ix_absence_poll_student_open", table_name="absence_poll")
    op.drop_table("absence_poll")
