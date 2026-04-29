"""Сервис атрибуции guest_attempt → user после авторизации (Phase Y-1).

Имя файла приведено в соответствие назначению в Phase Y-3 (cleanup).
Старое имя `link_token_service.py` сейчас занято под one-time link_token
для identity linking (см. tech-spec Y-3 backend §7.1, §7.3).
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.guest_attempt import GuestAttempt
from app.models.guest_session import GuestSession

logger = logging.getLogger(__name__)


async def attribute_guest_session(
    db: AsyncSession,
    guest_session_id: str,
    user_id: int,
) -> int:
    """
    Привязать guest_session и все её попытки к пользователю.
    Возвращает количество обновлённых попыток.
    """
    now = datetime.now(timezone.utc)

    await db.execute(
        update(GuestSession)
        .where(GuestSession.id == guest_session_id)
        .values(attributed_user_id=user_id)
    )

    result = await db.execute(
        update(GuestAttempt)
        .where(
            GuestAttempt.guest_session_id == guest_session_id,
            GuestAttempt.attributed_user_id.is_(None),
        )
        .values(attributed_user_id=user_id, attributed_at=now)
    )
    await db.flush()
    count: int = result.rowcount
    logger.info(
        "attribute_guest_session: session=%s user_id=%d attempts=%d",
        guest_session_id,
        user_id,
        count,
    )
    return count
