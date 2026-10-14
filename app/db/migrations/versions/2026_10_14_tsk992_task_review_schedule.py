"""tsk-992 (П3): повторение по следам ошибок — расписание и рубильник курса.

Решение оператора Р2: на повтор ставятся только задания «сначала завалил,
потом сдал»; интервалы 3 → 7 → 21 день, незачёт на повторе — откат к 3 дням.

- `task_review_schedule` — одна строка на (ученик, задание, корневой курс):
  стадия интервала, срок следующего повтора, последний результат. После
  верного повтора на стадии 2 строка закрывается (`completed_at`), а не
  удаляется — история «закрепил» остаётся читаемой.
- `courses.retrieval_config JSONB NULL` — рубильник на корневом курсе по
  образцу `sampling_config`: `{"enabled": false}`. NULL и `enabled=false`
  значат одно и то же — повторение выключено, поведение прежнее.

Обе части только добавляются: старые строки и старые клиенты не затронуты.

Rollback: `alembic downgrade tsk1335_pd_consents` — таблица и колонка
снимаются, расписание повторов теряется (сами результаты повторов в
`task_results` с `source_system='retrieval'` остаются).

Revision ID: tsk992_task_review_schedule
Revises: tsk1335_pd_consents
Create Date: 2026-10-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk992_task_review_schedule"
down_revision: Union[str, None] = "tsk1335_pd_consents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Создать `task_review_schedule` и колонку `courses.retrieval_config`."""
    op.add_column(
        "courses",
        sa.Column(
            "retrieval_config",
            postgresql.JSONB(),
            nullable=True,
            comment=(
                "Повторение по следам ошибок (tsk-992): {enabled}. "
                "NULL/enabled=false — выключено."
            ),
        ),
    )
    op.create_table(
        "task_review_schedule",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "student_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "course_id",
            sa.Integer(),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=False,
            comment="Корневой курс, в контексте которого шло обучение",
        ),
        sa.Column(
            "interval_stage",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Стадия интервала: 0 → 3 дня, 1 → 7 дней, 2 → 21 день",
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_result_id",
            sa.Integer(),
            sa.ForeignKey("task_results.id", ondelete="SET NULL"),
            nullable=True,
            comment="Результат, по которому выставлен текущий срок",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Верный повтор на стадии 2 — задание закреплено",
        ),
        sa.CheckConstraint(
            "interval_stage BETWEEN 0 AND 2",
            name="ck_task_review_schedule_stage",
        ),
        sa.UniqueConstraint(
            "student_id",
            "task_id",
            "course_id",
            name="uq_task_review_schedule_student_task_course",
        ),
        comment="Расписание повторов заданий «сначала завалил, потом сдал» (tsk-992)",
    )
    op.create_index(
        "ix_task_review_schedule_due_open",
        "task_review_schedule",
        ["student_id", "course_id", "due_at"],
        postgresql_where=sa.text("completed_at IS NULL"),
    )


def downgrade() -> None:
    """Снять таблицу расписания и рубильник курса."""
    op.drop_index("ix_task_review_schedule_due_open", table_name="task_review_schedule")
    op.drop_table("task_review_schedule")
    op.drop_column("courses", "retrieval_config")
