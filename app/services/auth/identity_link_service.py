"""Сервис управления identity_link (поиск, создание, привязка).

Поддерживает двустороннюю синхронизацию users.tg_id ↔ identity_link kind='tg'
(см. ADR-0021 §3, Phase Y-1.5).
"""
import logging
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity_link import IdentityLink
from app.models.users import Users

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
