"""tsk-1139 итерация 2: квиз-воронка по правилам контента вместо курсов-веток.

Решение оператора 26.09: движок доращивается под модель контента
(`quiz-razvilka.lms.json`) — один курс-квиз, ветки и переходы внутри него по
правилам. Связка «входной квиз → курсы-ветки» итерации 1 больше не нужна.

Что делает:
- снимает `quiz_funnel_branch` (не выкатывалась, данных нет);
- `quiz_funnel_spec` — спецификация квиза из контента как есть: ветки вопросов с
  условиями показа и переходами, производные признаки, итоги со своими текстами
  и кнопками. Вопросы — задания курса (для гостевых попыток), правила — здесь:
  `TaskOption` лишние поля отбрасывает;
- `quiz_funnel_progress` — прохождение гостем: параметры ссылки, ветка, роль,
  итог — для замеров по веткам без пересчёта правил.

Rollback: `alembic downgrade tsk1139_quiz_funnel` — снимает обе таблицы и
возвращает пустую `quiz_funnel_branch`; спецификация и прогресс теряются.

Revision ID: tsk1139_quiz_funnel_spec
Revises: tsk1139_quiz_funnel
Create Date: 2026-09-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk1139_quiz_funnel_spec"
down_revision: Union[str, None] = "tsk1139_quiz_funnel"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Спецификация и прогресс вместо курсов-веток.

    Проверки существования — только ради локальной dev-базы, где черновик этой
    схемы уже побывал под ревизией итерации 1; на чистой базе они ничего не меняют.
    """
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    op.execute("DROP TABLE IF EXISTS quiz_funnel_branch")
    if "quiz_funnel_spec" in existing and "quiz_funnel_progress" in existing:
        return

    op.create_table(
        "quiz_funnel_spec",
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("course_id"),
        comment="Спецификация квиза-воронки: ветки, условия, итоги, тексты (tsk-1139)",
    )

    op.create_table(
        "quiz_funnel_progress",
        sa.Column("guest_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=True),
        sa.Column("branch", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("outcome_code", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["guest_session_id"], ["guest_session.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("guest_session_id", "course_id"),
        comment="Прохождение квиза-воронки гостем: параметры ссылки, ветка, итог (tsk-1139)",
    )


def downgrade() -> None:
    """Вернуть таблицу курсов-веток итерации 1 (пустой)."""
    op.drop_table("quiz_funnel_progress")
    op.drop_table("quiz_funnel_spec")
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
