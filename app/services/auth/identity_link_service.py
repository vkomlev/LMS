"""Сервис управления identity_link (поиск, создание, привязка).

Поддерживает двустороннюю синхронизацию users.tg_id ↔ identity_link kind='tg'
(см. ADR-0021 §3, Phase Y-1.5).

Phase Y-3 добавляет `link_existing_user` для привязки новой identity к
уже залогиненному пользователю (см. tech-spec Y-3 backend §5.6, §7.4).
"""
import logging
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity_link import IdentityLink
from app.models.users import Users
from app.services.auth.exceptions import IdentityConflictError

logger = logging.getLogger(__name__)

IdentityKind = Literal["email", "tg", "vk"]


async def find_identity(
    db: AsyncSession,
    kind: IdentityKind,
    value: str,
) -> IdentityLink | None:
    """Найти identity_link по kind+value; value для email нормализуется в lower()."""
    normalized = value.lower() if kind == "email" else value
    result = await db.execute(
        select(IdentityLink).where(
            IdentityLink.kind == kind,
            IdentityLink.value == normalized,
        )
    )
    return result.scalar_one_or_none()


async def get_user_by_identity(
    db: AsyncSession,
    kind: IdentityKind,
    value: str,
) -> Users | None:
    """Найти пользователя через identity_link."""
    link = await find_identity(db, kind, value)
    if link is None:
        return None
    result = await db.execute(select(Users).where(Users.id == link.user_id))
    return result.scalar_one_or_none()


async def upsert_identity(
    db: AsyncSession,
    user_id: int,
    kind: IdentityKind,
    value: str,
    *,
    vk_access_token_enc: bytes | None = None,
    vk_refresh_token_enc: bytes | None = None,
    vk_token_expires_at=None,
) -> IdentityLink:
    """Создать или обновить identity_link.

    Для kind='tg' дополнительно синхронизирует users.tg_id (см. ADR-0021 §3).
    """
    normalized = value.lower() if kind == "email" else value
    link = await find_identity(db, kind, normalized)
    if link is None:
        link = IdentityLink(user_id=user_id, kind=kind, value=normalized)
        db.add(link)
    if vk_access_token_enc is not None:
        link.vk_access_token_enc = vk_access_token_enc
    if vk_refresh_token_enc is not None:
        link.vk_refresh_token_enc = vk_refresh_token_enc
    if vk_token_expires_at is not None:
        link.vk_token_expires_at = vk_token_expires_at
    await db.flush()

    if kind == "tg":
        await _sync_users_tg_id(db, user_id, int(value))

    return link


async def _sync_users_tg_id(db: AsyncSession, user_id: int, tg_id: int) -> None:
    """Идемпотентно обновить users.tg_id если отличается. См. ADR-0021 §3.

    Использует IS DISTINCT FROM для корректной обработки NULL (стандартное
    `!=` с NULL возвращает NULL → строка не matched).
    """
    await db.execute(
        update(Users)
        .where(Users.id == user_id, Users.tg_id.is_distinct_from(tg_id))
        .values(tg_id=tg_id)
    )


async def _kinds_of_user(db: AsyncSession, user_id: int) -> list[str]:
    """Список kind значений identity_link записей пользователя (для 409 details)."""
    result = await db.execute(
        select(IdentityLink.kind).where(IdentityLink.user_id == user_id)
    )
    return sorted({row[0] for row in result.all()})


async def link_existing_user(
    db: AsyncSession,
    user_id: int,
    kind: IdentityKind,
    value: str,
    *,
    vk_access_token_enc: bytes | None = None,
    vk_refresh_token_enc: bytes | None = None,
    vk_token_expires_at: datetime | None = None,
) -> IdentityLink:
    """Привязать новую identity к уже залогиненному пользователю (Phase Y-3).

    Семантика:
    - Если (kind, value) уже привязан к этому же `user_id` → idempotent success
      (обновляется `last_used_at`).
    - Если привязан к ДРУГОМУ user → `IdentityConflictError(conflict_kind="<kind>_already_linked",
      existing_kinds=<kinds владельца>)` → router маппит в HTTP 409.
    - Для kind='email' дополнительно orphan-check (Y-1.5.1 lesson): если в `users.email`
      есть запись без identity_link → `IdentityConflictError(conflict_kind=
      "email_already_linked_to_orphan_user", existing_kinds=[])`.
    - INSERT identity_link выполняется в SAVEPOINT через `db.begin_nested()` —
      IntegrityError на concurrent partial unique violation откатывает только nested
      транзакцию (Y-1.5 lesson #3 — НЕ `db.rollback()`).
    - Для kind='tg' двусторонняя sync с `users.tg_id` (через `_sync_users_tg_id`).

    Caller обязан выполнить `db.commit()` после успешного return — этот метод только flush().
    """
    normalized = value.lower() if kind == "email" else value

    # 1. Существующий identity_link с этим (kind, value)?
    existing = await find_identity(db, kind, normalized)
    if existing is not None:
        if existing.user_id == user_id:
            existing.last_used_at = datetime.now(timezone.utc)
            await db.flush()
            return existing
        # Принадлежит другому user — 409 (защита от identity-takeover, ADR-0021 §2)
        owner_kinds = await _kinds_of_user(db, existing.user_id)
        raise IdentityConflictError(
            conflict_kind=f"{kind}_already_linked",
            existing_kinds=owner_kinds,
        )

    # 2. Для email — orphan-check (Y-1.5.1)
    if kind == "email":
        orphan = (
            await db.execute(
                select(Users).where(func.lower(Users.email) == normalized)
            )
        ).scalar_one_or_none()
        if orphan is not None and orphan.id != user_id:
            raise IdentityConflictError(
                conflict_kind="email_already_linked_to_orphan_user",
                existing_kinds=[],
            )

    # 3. INSERT через savepoint (Y-1.5 lesson #3)
    try:
        async with db.begin_nested():
            link = await upsert_identity(
                db,
                user_id,
                kind,
                normalized,
                vk_access_token_enc=vk_access_token_enc,
                vk_refresh_token_enc=vk_refresh_token_enc,
                vk_token_expires_at=vk_token_expires_at,
            )
    except IntegrityError:
        # Concurrent INSERT победил — race resolved, пробуем найти результат
        race_winner = await find_identity(db, kind, normalized)
        if race_winner is None:
            raise
        if race_winner.user_id == user_id:
            return race_winner
        owner_kinds = await _kinds_of_user(db, race_winner.user_id)
        raise IdentityConflictError(
            conflict_kind=f"{kind}_already_linked",
            existing_kinds=owner_kinds,
        )

    logger.info("identity.linked user_id=%d kind=%s", user_id, kind)
    return link
