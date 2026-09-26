"""tsk-1139: воронка сайта — квиз с развилкой, метки источника, гость в боте.

Оператор 26.09: один входной квиз («для кого подбираем») и 4 квиза-ветки; итог
ветки ведёт в регистрацию, бот выдаёт PDF ветки и напоминает о пробном.

Что делает:
- `guest_session.attribution` jsonb — метки первого касания (utm_*, page, for,
  referrer, entry_uid). Пишутся один раз: следующий квиз той же сессии их не
  перетирает, иначе источник подменился бы на «переход с нашего же квиза»;
- `leads.attribution` jsonb — копия меток сессии + ветка + согласие; колонок под
  каждую метку не заводим — набор меток меняется вместе со схемой tsk-1072;
- `quiz_funnel_branch` — связка «входной квиз → вариант ответа → квиз-ветка»
  и настройки ветки (PDF, открыта ли регистрация). Отдельной таблицей, а не
  полем варианта ответа: `TaskOption` лишние поля отбрасывает, и связь молча
  терялась бы при каждом разборе задания;
- `quiz_funnel_bot_lead` — гость ветки в ученическом боте: токен стартовой
  ссылки, tg_id после /start, шаг напоминаний, отписка, запись на пробное.

Rollback: `alembic downgrade tsk1124_schedule_groups` — снимает обе таблицы и
обе колонки; метки и гости бота теряются (до включения рубильника их нет).

Revision ID: tsk1139_quiz_funnel
Revises: tsk1124_schedule_groups
Create Date: 2026-09-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk1139_quiz_funnel"
down_revision: Union[str, None] = "tsk1124_schedule_groups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Колонки меток и две таблицы воронки."""
    op.add_column(
        "guest_session",
        sa.Column(
            "attribution", postgresql.JSONB(), nullable=True,
            comment="Метки первого касания: utm_*, page, for, referrer, entry_uid (tsk-1139)",
        ),
    )
    op.add_column(
        "leads",
        sa.Column(
            "attribution", postgresql.JSONB(), nullable=True,
            comment="Метки источника, ветка квиза, согласие (tsk-1139)",
        ),
    )

    op.create_table(
        "quiz_funnel_branch",
        sa.Column("quiz_course_id", sa.Integer(), nullable=False),
        sa.Column("entry_course_id", sa.Integer(), nullable=False),
        sa.Column("branch_code", sa.Text(), nullable=False),
        sa.Column("entry_option_id", sa.Text(), nullable=False),
        sa.Column("pdf_url", sa.Text(), nullable=True),
        sa.Column(
            "registration_enabled", sa.Boolean(), nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["quiz_course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entry_course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("quiz_course_id"),
        sa.UniqueConstraint("entry_course_id", "branch_code", name="uq_quiz_funnel_branch_code"),
        sa.UniqueConstraint(
            "entry_course_id", "entry_option_id", name="uq_quiz_funnel_branch_option"
        ),
        sa.CheckConstraint(
            "branch_code ~ '^[a-z][a-z0-9_]{0,31}$'", name="ck_quiz_funnel_branch_code"
        ),
        comment="Ветки входного квиза воронки сайта (tsk-1139)",
    )

    op.create_table(
        "quiz_funnel_bot_lead",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("start_token", sa.Text(), nullable=False),
        sa.Column("guest_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quiz_course_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("tg_id", sa.BigInteger(), nullable=True),
        sa.Column("tg_username", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "reminder_step", sa.SmallInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("next_reminder_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["guest_session_id"], ["guest_session.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["quiz_course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("start_token", name="uq_quiz_funnel_bot_lead_token"),
        sa.UniqueConstraint(
            "guest_session_id", "quiz_course_id", name="uq_quiz_funnel_bot_lead_session"
        ),
        comment="Гость ветки квиза в ученическом боте: PDF, напоминания, пробное (tsk-1139)",
    )
    op.create_index(
        "ix_quiz_funnel_bot_lead_due",
        "quiz_funnel_bot_lead",
        ["next_reminder_at"],
        postgresql_where=sa.text("next_reminder_at IS NOT NULL AND unsubscribed_at IS NULL"),
    )


def downgrade() -> None:
    """Снять таблицы и колонки воронки."""
    op.drop_index("ix_quiz_funnel_bot_lead_due", table_name="quiz_funnel_bot_lead")
    op.drop_table("quiz_funnel_bot_lead")
    op.drop_table("quiz_funnel_branch")
    op.drop_column("leads", "attribution")
    op.drop_column("guest_session", "attribution")
