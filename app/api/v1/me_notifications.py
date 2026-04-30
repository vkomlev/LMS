"""Эндпоинты /me/notifications/* — inbox для ученика (Phase Y-4).

GET  /me/notifications/unread-count   — количество непрочитанных
GET  /me/notifications                 — список с пагинацией и фильтром unread_only
POST /me/notifications/{id}/read       — пометить прочитанной (idempotent)

См. tech-spec Y-4 (LMS-side) §4.2.2-4.2.4.

Безопасность IDOR:
- list/unread_count фильтруют WHERE user_id = current_user.id
- mark_read проверяет owner (UPDATE ... WHERE id=:id AND user_id=:current_user.id)
  и при mismatch возвращает 403 / 404 / 200-idempotent
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_authenticated
from app.auth.current_user import CurrentUser
from app.schemas.me_notifications import (
    MarkReadResponse,
    NotificationRead,
    UnreadCountResponse,
)
from app.services import audit_service, inbox_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/me/notifications", tags=["me_notifications"])


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    summary="Количество непрочитанных уведомлений (Phase Y-4)",
)
async def get_unread_count(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> UnreadCountResponse:
    count = await inbox_service.unread_count(db, current_user.id)
    return UnreadCountResponse(
        count=count, last_check_at=datetime.now(timezone.utc)
    )


@router.get(
    "",
    response_model=list[NotificationRead],
    summary="Список inbox-уведомлений с пагинацией (Phase Y-4)",
)
async def list_notifications(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
    limit: int = Query(50, ge=1, le=100, description="Лимит (max 100)"),
    offset: int = Query(0, ge=0, description="Смещение"),
    unread_only: bool = Query(False, description="Только непрочитанные"),
) -> list[NotificationRead]:
    items = await inbox_service.list_for_user(
        db,
        user_id=current_user.id,
        limit=limit,
        offset=offset,
        unread_only=unread_only,
    )
    return [
        NotificationRead(
            id=item.id,
            kind=item.kind,
            title=item.title,
            content=item.content,
            payload=item.payload,
            created_at=item.modified_at,
            read_at=item.read_at,
            is_unread=item.read_at is None,
        )
        for item in items
    ]


@router.post(
    "/{notification_id}/read",
    response_model=MarkReadResponse,
    summary="Пометить уведомление прочитанным (idempotent, Phase Y-4)",
    responses={
        200: {"description": "Помечено или уже прочитано (idempotent)"},
        403: {"description": "Запись принадлежит другому пользователю"},
        404: {"description": "Запись не найдена"},
    },
)
async def mark_notification_read(
    request: Request,
    notification_id: int = Path(..., ge=1),
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> MarkReadResponse:
    ip = request.client.host if request.client else "unknown"

    # Атомарная попытка UPDATE — самый частый путь
    new_read_at = await inbox_service.mark_read(
        db, notification_id, current_user.id
    )
    if new_read_at is not None:
        # Successful update — записываем audit и коммитим
        await audit_service.log_event(
            db,
            audit_service.STUDENT_NOTIFICATION_READ,
            user_id=current_user.id,
            ip=ip,
            details={"notification_id": notification_id},
        )
        await db.commit()
        return MarkReadResponse(id=notification_id, read_at=new_read_at)

    # rowcount=0 — различаем 404 / 403 / 200-idempotent
    status_pair = await inbox_service.get_status(db, notification_id)
    if status_pair is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Запись не найдена")
    owner_id, existing_read_at = status_pair
    if owner_id != current_user.id:
        # IDOR защита: чужая запись или legacy-запись с user_id=NULL
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Запись принадлежит другому пользователю"
        )
    # Уже прочитана — idempotent 200, без audit (по spec'у §4.2.4)
    if existing_read_at is None:
        # Race-окно: параллельный POST mark_read (дубликат из другой вкладки или
        # повтор после network glitch) выиграл UPDATE, но его транзакция ещё не
        # commit'нулась к моменту нашего SELECT (snapshot изоляция).
        # Корректный ответ для idempotent endpoint — 200 OK с серверным now()
        # (клиент не должен видеть 500 за benign дубликат). Audit пропускаем:
        # запись помечается в параллельной транзакции, дубликат не нужен.
        logger.info(
            "mark_read race-window: дубликат POST для notification %s user %s — "
            "возвращаем 200 idempotent (now())",
            notification_id, current_user.id,
        )
        return MarkReadResponse(
            id=notification_id, read_at=datetime.now(timezone.utc)
        )
    return MarkReadResponse(id=notification_id, read_at=existing_read_at)
