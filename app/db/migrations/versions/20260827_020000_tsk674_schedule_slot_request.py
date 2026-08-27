"""tsk-674 фаза 3: заявки «не нашёл подходящее время».

Контекст. Фаза 1 собрала пожелания, фаза 2 дала методисту сверстать сетку.
После вёрстки новый ученик выбирает время сам — из свободных и частично
свободных слотов. Но у части людей подходящего часа в сетке не окажется, и
для них есть кнопка: заявка уходит методисту вместе с пожеланиями, а тот либо
добавляет слот, либо договаривается на существующий.

Почему таблица, а не только уведомление методисту. Уведомление — это сигнал, а
сигнал в этой школе уже дважды тонул: механизм срабатывал, письмо уходило и
оставалось непрочитанным (tsk-591, tsk-652). Таблица со статусом даёт очередь,
в которой видно, что ещё не разобрано, — и по ней же считается счётчик в
кабинете методиста. Уведомление никуда не девается, оно ведёт в эту очередь.

Снимок пожеланий (`hours`, `lessons_per_week`) лежит в самой заявке, а не
читается по ссылке: методист разбирает её через день-другой, а ученик к тому
времени мог поправить пожелания. Разговор должен идти о том, с чем человек
нажал кнопку.

Частичный уникальный индекс по `student_id WHERE status = 'open'`: у одного
ученика не может быть двух открытых заявок. Повторное нажатие обновляет
существующую и шлёт методисту напоминание — а не плодит очередь из одного
человека.

Rollback: `alembic downgrade tsk692_tasks_created_at` — таблица удаляется
вместе с необработанными заявками. Перед откатом после запуска записи стоит
свериться, что открытых заявок нет: восстанавливать их будет неоткуда.

Revision ID: tsk674_schedule_slot_request
Revises: tsk692_tasks_created_at
Create Date: 2026-08-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk674_schedule_slot_request"
down_revision: Union[str, None] = "tsk692_tasks_created_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "schedule_slot_request",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column(
            "comment", sa.Text(), nullable=True,
            comment="Что именно не подошло — словами ученика",
        ),
        sa.Column(
            "lessons_per_week", sa.SmallInteger(), nullable=False,
            server_default=sa.text("2"),
            comment="Снимок: сколько занятий в неделю просил ученик на момент заявки",
        ),
        sa.Column(
            "hours", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="Снимок пожеланий: [{weekday, start_time, kind}, …], время московское",
        ),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default=sa.text("'open'"),
            comment="open — ждёт методиста, resolved — разобрано",
        ),
        sa.Column(
            "resolution_note", sa.Text(), nullable=True,
            comment="Чем кончилось: добавили слот / договорились / записался сам",
        ),
        sa.Column("resolved_by", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="schedule_slot_request_student_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"], ["users.id"], ondelete="SET NULL",
            name="schedule_slot_request_resolved_by_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="schedule_slot_request_pkey"),
        sa.CheckConstraint(
            "status IN ('open', 'resolved')", name="ck_schedule_slot_request_status"
        ),
        comment="Заявки учеников «не нашёл подходящее время» (tsk-674 фаза 3)",
    )

    op.create_index(
        "uq_schedule_slot_request_open_student",
        "schedule_slot_request",
        ["student_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    # Очередь методиста читается «открытые сверху, свежие первыми».
    op.create_index(
        "ix_schedule_slot_request_status_created",
        "schedule_slot_request",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_schedule_slot_request_status_created", table_name="schedule_slot_request")
    op.drop_index("uq_schedule_slot_request_open_student", table_name="schedule_slot_request")
    op.drop_table("schedule_slot_request")
