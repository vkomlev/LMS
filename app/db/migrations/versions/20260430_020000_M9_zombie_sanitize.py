"""M9: data-migration — санация zombie task_results под Y-4.2 R-3 fix.

Revision ID: m9_zombie_sanitize
Revises: 20260430_010000_m8_inbox
Create Date: 2026-04-30

Закрывает R-3 finding: после фикса фильтра в claim_next_review/pending_count/
list_pending_review/workload (Y-4.2 spec §9.1-9.3) автопроверенные MC/SC/SA
с `is_correct IS NOT NULL AND checked_at IS NULL` больше не попадают в очередь
ручной проверки. Существующие записи (10 zombies на 2026-04-30, verified MCP)
санируются: `checked_at = COALESCE(received_at, submitted_at, now())`.

Безопасность:
- На 2026-04-30: 10 zombies (4 SA + 3 MC + 3 SC), single UPDATE безопасен
  (lock < 1 сек на таком объёме).
- Идемпотентность: повторный upgrade match'ит 0 строк (предикат уже не выполнен).
- Pending записи (`is_correct IS NULL`) — НЕ затрагиваются.
- Уже-проверенные вручную (`checked_at IS NOT NULL`) — НЕ затрагиваются.

Downgrade невозможен: мы не знаем, какие checked_at были оригинально NULL
vs какие выставлены этой миграцией. No-op с warning.

См. LMS Y-4.2 spec §9.4 + CB authority §«Y-4.2 R-3 fix».
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "m9_zombie_sanitize"
down_revision: Union[str, None] = "20260430_010000_m8_inbox"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE task_results
        SET checked_at = COALESCE(received_at, submitted_at, now())
        WHERE is_correct IS NOT NULL
          AND checked_at IS NULL
        """
    )


def downgrade() -> None:
    # Невозможно восстановить оригинальное распределение NULL/NOT NULL
    # на checked_at. M9 — forward-only data fix. Помечаем downgrade
    # явным no-op SELECT с диагностикой для отладочных runs.
    op.execute(
        "SELECT 'm9_zombie_sanitize downgrade: no-op (forward-only data fix)' AS warning"
    )
