"""M8: расширение notifications таблицы под inbox-семантику (Phase Y-4).

Revision ID: 20260430_010000_m8_inbox
Revises: 20260429_010000_m7_streak_idx
Create Date: 2026-04-30

Расширяет legacy `notifications` таблицу (исторически = template_versions, count=0)
пятью nullable полями для inbox-уведомлений ученикам:
  - user_id      INT  FK users.id ON DELETE CASCADE  — получатель
  - kind         VARCHAR(64)                        — тип (sa_com_graded, …)
  - title        VARCHAR(255)                       — короткий заголовок
  - payload      JSONB                              — структурированные данные
  - read_at      TIMESTAMPTZ                        — когда ученик прочитал

Плюс два partial-индекса для быстрого unread-count и list-by-user.

Безопасность:
- На 2026-04-30 в `notifications` 0 записей (verified MCP) — миграция не задевает данные.
- Все новые колонки nullable → legacy-семантика (template-версии) сохраняется.
- Legacy PK `template_versions_pkey` и FK `template_versions_modified_by_fkey`
  не трогаем (имена были даны при первоначальном создании таблицы).

Длина revision_id ограничена 32 символами (alembic_version.version_num VARCHAR(32)).

См. CB authority Y-4 §7.3 + LMS-side spec
docs/specs/2026-04-30-tech-spec-Y4-sa-com-teacher-queue-backend.md §5.1.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260430_010000_m8_inbox"
down_revision: Union[str, None] = "20260429_010000_m7_streak_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("user_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("kind", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("title", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "notifications",
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_foreign_key(
        "fk_notifications_user_id",
        "notifications",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_index(
        "idx_notifications_user_unread",
        "notifications",
        ["user_id", sa.text("modified_at DESC")],
        postgresql_where=sa.text("user_id IS NOT NULL AND read_at IS NULL"),
    )
    op.create_index(
        "idx_notifications_user_all",
        "notifications",
        ["user_id", sa.text("modified_at DESC")],
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_notifications_user_all", table_name="notifications")
    op.drop_index("idx_notifications_user_unread", table_name="notifications")
    op.drop_constraint(
        "fk_notifications_user_id", "notifications", type_="foreignkey"
    )
    op.drop_column("notifications", "read_at")
    op.drop_column("notifications", "payload")
    op.drop_column("notifications", "title")
    op.drop_column("notifications", "kind")
    op.drop_column("notifications", "user_id")
