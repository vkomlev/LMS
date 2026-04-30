"""InboxService — inbox-уведомления ученикам (Phase Y-4).

Хранит inbox-записи поверх расширенной М8 таблицы `notifications`.
Используется внутри grade-flow преподавателя и эндпоинтов /me/notifications/*.

Безопасность:
- Все запросы scoped по user_id current_user (IDOR защита в endpoint layer).
- mark_read атомарен: UPDATE ... WHERE id=:id AND user_id=:uid AND read_at IS NULL
  RETURNING id — без race window между SELECT и UPDATE.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notifications import Notifications

logger = logging.getLogger(__name__)


async def create_for_user(
    db: AsyncSession,
    *,
    user_id: int,
    kind: str,
    title: str,
    content: str,
    payload: dict[str, Any],
    created_by: Optional[int],
) -> Notifications:
    """Создать inbox-запись для пользователя.

    Не коммитит сам — caller отвечает за транзакцию (используется внутри
    grade-flow, где задействована одна транзакция).
    """
    item = Notifications(
        content=content,
        modified_by=created_by,
        user_id=user_id,
        kind=kind,
        title=title,
        payload=payload,
        read_at=None,
    )
    db.add(item)
    await db.flush()
    await db.refresh(item)
    return item


async def list_for_user(
    db: AsyncSession,
    *,
    user_id: int,
    limit: int,
    offset: int,
    unread_only: bool,
) -> list[Notifications]:
    """Список inbox-записей пользователя, ORDER BY modified_at DESC."""
    # SQL template с переключаемым WHERE-фрагментом; в template вставляется
    # только литерал из 2 вариантов (никакого user-input). Все динамические
    # значения идут через bind (user_id, limit, offset).
    unread_clause = "AND read_at IS NULL" if unread_only else ""
    sql = (
        "SELECT id, content, modified_by, modified_at, "
        "       user_id, kind, title, payload, read_at "
        "FROM notifications "
        "WHERE user_id = :user_id "
        + unread_clause + " "  # nosec B608 — unread_clause из 2 литералов, не user-input
        "ORDER BY modified_at DESC "
        "LIMIT :limit OFFSET :offset"
    )
    result = await db.execute(
        text(sql),
        {"user_id": user_id, "limit": limit, "offset": offset},
    )
    rows = result.mappings().all()
    items: list[Notifications] = []
    for row in rows:
        n = Notifications(
            id=row["id"],
            content=row["content"],
            modified_by=row["modified_by"],
            modified_at=row["modified_at"],
            user_id=row["user_id"],
            kind=row["kind"],
            title=row["title"],
            payload=row["payload"],
            read_at=row["read_at"],
        )
        items.append(n)
    return items


async def unread_count(db: AsyncSession, user_id: int) -> int:
    """Количество непрочитанных уведомлений пользователя."""
    result = await db.execute(
        text(
            "SELECT count(*) FROM notifications "
            "WHERE user_id = :user_id AND read_at IS NULL"
        ),
        {"user_id": user_id},
    )
    return int(result.scalar() or 0)


async def mark_read(
    db: AsyncSession,
    notification_id: int,
    user_id: int,
) -> Optional[datetime]:
    """Атомарно пометить запись прочитанной.

    Возвращает read_at если UPDATE сработал (rowcount=1), иначе None
    (запись уже прочитана / не существует / принадлежит другому user).
    Caller должен сам различать ветви через дополнительный SELECT,
    если нужны разные HTTP коды (404/403/200-idempotent).
    """
    result = await db.execute(
        text(
            "UPDATE notifications SET read_at = now() "
            "WHERE id = :nid AND user_id = :uid AND read_at IS NULL "
            "RETURNING read_at"
        ),
        {"nid": notification_id, "uid": user_id},
    )
    row = result.fetchone()
    if row is None:
        return None
    return row[0]


async def get_status(
    db: AsyncSession,
    notification_id: int,
) -> Optional[tuple[int, Optional[datetime]]]:
    """Вернуть (owner_user_id, read_at) для notification или None если не существует.

    Используется endpoint'ом для различения 404 / 403 / 200-idempotent после
    failed mark_read.
    """
    result = await db.execute(
        text(
            "SELECT user_id, read_at FROM notifications WHERE id = :nid"
        ),
        {"nid": notification_id},
    )
    row = result.fetchone()
    if row is None:
        return None
    owner_id, read_at = row
    return (int(owner_id) if owner_id is not None else 0, read_at)
