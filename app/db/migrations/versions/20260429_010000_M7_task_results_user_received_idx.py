"""M7: индекс task_results(user_id, received_at DESC) для streak query (Phase Y-3).

Поддерживает GET /api/v1/me/streak (CTE с фильтрацией по user_id и сортировкой
по received_at DESC) — без индекса каждый запрос делает Seq Scan по партиции
user_id. См. tech-spec Y-3 (LMS-side) §6 и CB authority spec §7.2.7.

Revision ID: 20260429_010000_m7_streak_idx
Revises: 20260428_060000_m6_tg_sync
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa


revision = "20260429_010000_m7_streak_idx"
down_revision = "20260428_060000_m6_tg_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_task_results_user_received",
        "task_results",
        ["user_id", sa.text("received_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_task_results_user_received", table_name="task_results")
