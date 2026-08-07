"""tsk-572 фаза 7: сигналы о необходимости повторения.

**Почему сигналов два типа, а не один.** Датчик умеет заметить две разные вещи,
и адресаты у них разные:

- `student_id IS NULL` — проваливается ТЕМА: много учеников, высокая доля
  ошибок. Это заявка методисту на мини-курс повторения, работа с контентом.
- `student_id` задан — буксует КОНКРЕТНЫЙ ученик. Это сигнал ПРЕПОДАВАТЕЛЮ,
  потому что он работает с учеником вживую, а методист — нет.

Смешать их в один поток нельзя: методисту незачем разбирать личные затыки (он
не ведёт занятий), а преподавателю незачем получать заявки на переписывание
курса. Именно поэтому у сигнала есть `teacher_comment` — преподаватель видел
ученика живьём и знает то, чего в цифрах нет; его комментарий уезжает вместе с
эскалацией методисту и часто оказывается ценнее самой доли ошибок.

**Состояния.** `new` → преподаватель принял к сведению (`acknowledged`, при
желании с комментарием) → при необходимости `escalated` методисту. Либо
`dismissed`, если преподаватель знает, что повторение не нужно (ученик болел,
задание сломано, ошибка в эталоне). Отклонённый сигнал — не мусор: по нему
видно, что датчик шумит, и это основание пересмотреть пороги.

Идемпотентность: частичный уникальный индекс не даёт заводить второй открытый
сигнал по той же паре. Иначе cron за неделю создал бы семь одинаковых, и
преподаватель перестал бы их читать.

Rollback: `alembic downgrade tsk572_ai_tutor` — таблица удаляется вместе с
комментариями преподавателей. Перед откатом на проде снять дамп.

Revision ID: tsk572_gap_signals
Revises: tsk572_ai_tutor
Create Date: 2026-08-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk572_gap_signals"
down_revision: Union[str, None] = "tsk572_ai_tutor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "learning_gap_signal",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.Integer(), nullable=False,
                  comment="Тема, на которой спотыкаются"),
        sa.Column("student_id", sa.Integer(), nullable=True,
                  comment="NULL — тема целиком (методисту); задан — ученик (преподавателю)"),
        sa.Column("submissions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("students", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wrong_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="new",
                  comment="new | acknowledged | escalated | dismissed"),
        sa.Column("teacher_id", sa.Integer(), nullable=True,
                  comment="Кто принял к сведению"),
        sa.Column("teacher_comment", sa.Text(), nullable=True,
                  comment="Он видел ученика живьём — знает то, чего в цифрах нет"),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["teacher_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('new','acknowledged','escalated','dismissed')",
            name="ck_gap_signal_status",
        ),
        comment="tsk-572: сигнал «нужно повторение» — теме методисту, ученику преподавателю",
    )
    # Открытый сигнал на пару может быть только один: без этого cron за неделю
    # завёл бы семь одинаковых, и преподаватель перестал бы их читать.
    op.execute("""
        CREATE UNIQUE INDEX uq_gap_signal_open_topic ON learning_gap_signal (course_id)
        WHERE student_id IS NULL AND status IN ('new','acknowledged')
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_gap_signal_open_student
        ON learning_gap_signal (course_id, student_id)
        WHERE student_id IS NOT NULL AND status IN ('new','acknowledged')
    """)
    op.create_index("ix_gap_signal_status", "learning_gap_signal",
                    ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_gap_signal_status", table_name="learning_gap_signal")
    op.execute("DROP INDEX IF EXISTS uq_gap_signal_open_student")
    op.execute("DROP INDEX IF EXISTS uq_gap_signal_open_topic")
    op.drop_table("learning_gap_signal")
