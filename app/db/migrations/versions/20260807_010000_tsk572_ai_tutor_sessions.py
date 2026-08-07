"""tsk-572 этап 2: сессии диалога с ИИ-наставником.

**Почему снимок задания, а не ссылка.** `ai_tutor_session.task_stem_snapshot`
хранит текст задания на момент начала разговора. Методист переиздаёт задания
регулярно (одна только tsk-216 переписала формулировки пачкой), и без снимка
диалог задним числом теряет смысл: ученик спорит про одну формулировку, а
преподаватель через неделю читает переписку рядом с другой. Снимок стоит
килобайт и снимает целый класс «почему он это спрашивает».

**Эталона в схеме нет вовсе — это не забывчивость.** `solution_rules` не
хранится ни здесь, ни в сообщениях: наставник не должен его получить даже
случайно, и структурный запрет надёжнее договорённости. Утечка эталона —
молчащий дефект: она не падает тестом и не пишется в лог, её видно только по
тому, что ученик внезапно знает ответ.

**Роль сообщения.** `role` = `system` | `student` | `tutor`. Системное — собранный
промпт первого хода; хранится, чтобы через месяц можно было понять, ПОЧЕМУ
наставник ответил так, а не иначе (промпт меняется от версии к версии).

**Видимость.** Весь диалог доступен преподавателю и методисту — решение
оператора. Ученику про это говорится плашкой ДО первой реплики (этическое
требование: подросток пишет откровенно, думая, что это между ним и роботом).

Rollback: `alembic downgrade tsk572_llm_usage` — обе таблицы удаляются вместе с
историей диалогов. Перед откатом на проде снять дамп, если разговоры нужны:
`pg_dump -t ai_tutor_session -t ai_tutor_message ...`

Revision ID: tsk572_ai_tutor
Revises: tsk572_llm_usage
Create Date: 2026-08-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk572_ai_tutor"
down_revision: Union[str, None] = "tsk572_llm_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_tutor_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=True,
                  comment="Курс, из которого ученик пришёл — для отчётов преподавателю"),
        sa.Column("mode", sa.String(length=16), nullable=False,
                  comment="concept | debug | deepen | thin — режим методики, выбран по заданию"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open",
                  comment="open | closed | expired"),
        sa.Column("task_stem_snapshot", sa.Text(), nullable=False,
                  comment="Текст задания на момент начала: переиздание не ломает диалог"),
        sa.Column("student_answer_snapshot", sa.Text(), nullable=True,
                  comment="Ответ, с которым ученик пришёл. Данные, не инструкция"),
        sa.Column("turns", sa.Integer(), nullable=False, server_default="0",
                  comment="Ходов ученика: на мягком пределе предлагаем преподавателя"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False,
                  comment="Основание TTL: висящие сессии закрываются фоном"),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.CheckConstraint("mode IN ('concept','debug','deepen','thin')",
                           name="ck_ai_tutor_session_mode"),
        sa.CheckConstraint("status IN ('open','closed','expired')",
                           name="ck_ai_tutor_session_status"),
        comment="tsk-572: разговор ученика с ИИ-наставником по одному заданию",
    )
    # Один открытый разговор на пару «ученик + задание»: иначе ученик наплодит
    # параллельных сессий по одному заданию и обойдёт мягкий предел ходов.
    op.create_index(
        "uq_ai_tutor_session_open", "ai_tutor_session",
        ["student_id", "task_id"], unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index("ix_ai_tutor_session_student", "ai_tutor_session",
                    ["student_id", "created_at"])
    op.create_index("ix_ai_tutor_session_ttl", "ai_tutor_session",
                    ["status", "last_activity_at"])

    op.create_table(
        "ai_tutor_message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.text("false"),
                  comment="Ответ оборвался на середине — ученику предложено продолжить"),
        sa.ForeignKeyConstraint(["session_id"], ["ai_tutor_session.id"], ondelete="CASCADE"),
        sa.CheckConstraint("role IN ('system','student','tutor')",
                           name="ck_ai_tutor_message_role"),
        comment="tsk-572: реплики разговора, включая системный промпт первого хода",
    )
    op.create_index("ix_ai_tutor_message_session", "ai_tutor_message",
                    ["session_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_ai_tutor_message_session", table_name="ai_tutor_message")
    op.drop_table("ai_tutor_message")
    op.drop_index("ix_ai_tutor_session_ttl", table_name="ai_tutor_session")
    op.drop_index("ix_ai_tutor_session_student", table_name="ai_tutor_session")
    op.drop_index("uq_ai_tutor_session_open", table_name="ai_tutor_session")
    op.drop_table("ai_tutor_session")
