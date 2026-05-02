"""Сервис управления user_session (создание, валидация, отзыв)."""
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_session import UserSession

logger = logging.getLogger(__name__)

_TOKEN_BYTES = 32
# Y-5.2: продлеваем session TTL с 1ч до 24ч — иначе ученик за 1 урок (~30-60 мин)
# теряет сессию и должен снова логиниться. UX-блокер. 24 часа — удобный баланс
# (студент возвращается на следующий день; refresh — 30 дней).
_ACCESS_TTL_HOURS = 24
_REFRESH_TTL_DAYS = 30


def _hash_token(raw: bytes) -> bytes:
    return hashlib.sha256(raw).digest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_session(
    db: AsyncSession,
    user_id: int,
    ua_fingerprint: str | None = None,
) -> tuple[str, str, "UserSession"]:
    """
    Создать новую сессию.
    Возвращает (access_token, refresh_token, UserSession).
    """
    access_raw = os.urandom(_TOKEN_BYTES)
    refresh_raw = os.urandom(_TOKEN_BYTES)

    now = _now()
    session = UserSession(
        user_id=user_id,
        token_hash=_hash_token(access_raw),
        refresh_token_hash=_hash_token(refresh_raw),
        ua_fingerprint=ua_fingerprint,
        expires_at=now + timedelta(hours=_ACCESS_TTL_HOURS),
        refresh_expires_at=now + timedelta(days=_REFRESH_TTL_DAYS),
    )
    db.add(session)
    await db.flush()

    access_token = access_raw.hex()
    refresh_token = refresh_raw.hex()
    return access_token, refresh_token, session


async def validate_session(
    db: AsyncSession,
    access_token: str,
) -> "UserSession | None":
    """Проверить access_token; вернуть сессию если валидна и не истекла."""
    try:
        raw = bytes.fromhex(access_token)
    except ValueError:
        return None
    token_hash = _hash_token(raw)
    result = await db.execute(
        select(UserSession).where(
            UserSession.token_hash == token_hash,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > _now(),
        )
    )
    session = result.scalar_one_or_none()
    if session:
        session.last_used_at = _now()
        await db.flush()
    return session


async def refresh_session(
    db: AsyncSession,
    refresh_token: str,
) -> "tuple[str, str, UserSession] | None":
    """
    Выдать новую пару токенов по refresh_token.
    Старая сессия отзывается.
    """
    try:
        raw = bytes.fromhex(refresh_token)
    except ValueError:
        return None
    rh = _hash_token(raw)
    result = await db.execute(
        select(UserSession).where(
            UserSession.refresh_token_hash == rh,
            UserSession.revoked_at.is_(None),
            UserSession.refresh_expires_at > _now(),
        )
    )
    old = result.scalar_one_or_none()
    if old is None:
        return None
    old.revoked_at = _now()
    await db.flush()
    return await create_session(db, old.user_id, old.ua_fingerprint)


async def revoke_session(db: AsyncSession, session_id: UUID) -> None:
    """Отозвать конкретную сессию."""
    await db.execute(
        update(UserSession)
        .where(UserSession.id == session_id)
        .values(revoked_at=_now())
    )
    await db.flush()


async def revoke_all_sessions(db: AsyncSession, user_id: int) -> None:
    """Отозвать все активные сессии пользователя."""
    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    await db.flush()
