"""Сервис записи append-only событий в audit_event."""
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent

logger = logging.getLogger(__name__)


async def log_event(
    db: AsyncSession,
    event_type: str,
    user_id: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Записать событие в audit_event (INSERT только, UPDATE/DELETE запрещены триггером)."""
    event = AuditEvent(
        event_type=event_type,
        user_id=user_id,
        ip=ip,
        user_agent=user_agent,
        details=details,
    )
    db.add(event)
    try:
        await db.flush()
    except Exception:
        logger.exception("audit_event flush failed для %s", event_type)
        raise
