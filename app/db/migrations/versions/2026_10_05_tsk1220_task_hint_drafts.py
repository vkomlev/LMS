"""tsk-1220: черновики текстовых подсказок из ответов преподавателей.

Черновик живёт в отдельной таблице, а не в `task_content`: всё, что лежит в
содержимом задания, уходит ученику, а черновик до вычитки человеком видеть
ему нельзя. Отдельная строка хранит и то, из каких ответов он собран
(`source_reply_ids`) — еженедельная рутина берёт задание снова только тогда,
когда появились ответы, которых нет ни в одном черновике.

Статусы:
- `draft` — ждёт вычитки;
- `approved` — подтверждён человеком и дописан в `hints_text`;
- `rejected` — отклонён человеком;
- `skipped` — модель сочла, что из ответов нельзя вывести общий приём;
- `blocked` — линтер нашёл утечку ответа, в очередь не попал.

Последние два хранятся, чтобы рутина не генерировала по тем же ответам заново.

Rollback: `alembic downgrade tsk1198_transparent_subcourse` — таблица
снимается целиком; подтверждённые подсказки остаются в `hints_text`.

Revision ID: tsk1220_task_hint_drafts
Revises: tsk1198_transparent_subcourse
Create Date: 2026-10-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tsk1220_task_hint_drafts"
down_revision: Union[str, None] = "tsk1198_transparent_subcourse"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Таблица черновиков с CHECK на статус и индексом под очередь."""
    op.create_table(
        "task_hint_drafts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column(
            "source_reply_ids",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "lint_flags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "reviewed_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'rejected', 'skipped', 'blocked')",
            name="task_hint_drafts_status_check",
        ),
        sa.CheckConstraint(
            "status IN ('skipped', 'blocked') OR length(coalesce(text, '')) > 0",
            name="task_hint_drafts_text_check",
        ),
        comment="Черновики текстовых подсказок из ответов преподавателей (tsk-1220)",
    )
    op.create_index("ix_task_hint_drafts_status", "task_hint_drafts", ["status", "created_at"])
    op.create_index("ix_task_hint_drafts_task", "task_hint_drafts", ["task_id"])


def downgrade() -> None:
    """Снять таблицу."""
    op.drop_index("ix_task_hint_drafts_task", table_name="task_hint_drafts")
    op.drop_index("ix_task_hint_drafts_status", table_name="task_hint_drafts")
    op.drop_table("task_hint_drafts")
