"""M10: backfill роли `student` для users без role (Phase Y-4 pre-S5).

Revision ID: m10_role_backfill
Revises: m9_zombie_sanitize
Create Date: 2026-05-01

Закрывает bug pre-S5 §1: при auto-registration через magic-link / TG initData /
VK callback в Y-1.5 пользователю создавалась запись в `users` + `identity_link`,
но НЕ создавалась запись в `user_roles`. В результате 1147/1158 users (verified
MCP 2026-05-01) не имеют ни одной роли — RBAC-проверки на role-gated путях
будут деградировать.

Действия:
- INSERT в user_roles для всех users без роли — `student` (role_id=4).
- ON CONFLICT (user_id, role_id) DO NOTHING защищает от race с runtime
  auto-assign в auth-сервисах (Y-4 pre-S5 S-PRE-1.3).

Адаптации с CB authority:
- Schema `user_roles(user_id, role_id)` — composite PK (FK на roles), не
  колонка `role` STRING. Используем role_id=4 (verified MCP).
- В `users` НЕТ колонки `is_service` (это runtime-атрибут CurrentUser, не
  stored). Backfill ВСЕМ users без роли — service-key auth не создаёт user
  row (CurrentUser(id=0, is_service=True)), потому false-positives нет.

Безопасность:
- Идемпотентна (ON CONFLICT DO NOTHING + предикат `WHERE ur.user_id IS NULL`).
- Single UPDATE на ~1147 строках — lock <2 сек, acceptable.
- Downgrade NO-OP: невозможно отделить M10-вставленные rows от ручных INSERT
  без отдельного маркера; safer to keep forward-only (как M9 zombie sanitize).

См. CB authority pre-S5 §8.1 + §9.1.2; LMS-side spec
docs/specs/2026-05-01-tech-spec-Y4-pre-S5-auth-role-backend.md.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "m10_role_backfill"
down_revision: Union[str, None] = "m9_zombie_sanitize"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO user_roles (user_id, role_id)
        SELECT u.id, (SELECT id FROM roles WHERE name = 'student')
        FROM users u
        LEFT JOIN user_roles ur ON ur.user_id = u.id
        WHERE ur.user_id IS NULL
        ON CONFLICT (user_id, role_id) DO NOTHING
        """
    )


def downgrade() -> None:
    # Невозможно надёжно отделить M10-вставленные rows от ручных INSERT.
    # Forward-only data fix; помечаем downgrade явным no-op.
    op.execute(
        "SELECT 'm10_role_backfill downgrade: no-op (forward-only data fix)' AS warning"
    )
