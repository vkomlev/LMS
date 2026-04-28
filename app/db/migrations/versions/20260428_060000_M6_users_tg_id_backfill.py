"""M6: backfill users.tg_id ↔ identity_link kind='tg' (Phase Y-1.5).

Двухсторонняя синхронизация для существующих несоответствий перед deploy
auto-create логики (см. ADR-0021 §3, tech-spec Y-1.5 §6.6).

Step 1: заполнить users.tg_id из identity_link где value отличается или NULL.
Step 2: создать identity_link для existing users.tg_id без записи.

Revision ID: 20260428_060000_m6_tg_sync
Revises: 20260428_050000_m5_guest
Create Date: 2026-04-28
"""
from alembic import op


revision = "20260428_060000_m6_tg_sync"
down_revision = "20260428_050000_m5_guest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: users.tg_id ← identity_link.value (приведение типа bigint)
    op.execute(
        """
        UPDATE users u
        SET tg_id = il.value::bigint
        FROM identity_link il
        WHERE il.kind = 'tg'
          AND il.user_id = u.id
          AND (u.tg_id IS NULL OR u.tg_id::text != il.value);
        """
    )

    # Step 2: identity_link ← users.tg_id (для legacy users без identity_link записи)
    op.execute(
        """
        INSERT INTO identity_link (user_id, kind, value, created_at)
        SELECT u.id, 'tg', u.tg_id::text, u.created_at
        FROM users u
        WHERE u.tg_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM identity_link il
            WHERE il.kind = 'tg' AND il.user_id = u.id
          )
        ON CONFLICT (kind, value) DO NOTHING;
        """
    )


def downgrade() -> None:
    """Lossy downgrade: невозможно tracking pre-state.

    identity_link записи которые были до upgrade — остаются (часть продакшн данных).
    users.tg_id значения которые были NULL и заполнились из identity_link — остаются
    (откат не возвращает их в NULL: identity_link остаётся источником правды).
    Это приемлемо: forward-only migration, не lossless.
    """
    pass
