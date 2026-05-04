"""Y-6 Stage 4.4: GET /api/v1/methodist/escalations/pending.

Возвращает методисту список свежих эскалаций (review_escalated +
course_pending_review) — используется TG_LMS methodist-бот'ом
(`bots/methodist/poller.py` в Stage 5.1).

ACL: current_user должен иметь role=methodist (или быть service-key).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user
from app.auth.current_user import CurrentUser

router = APIRouter(prefix="/methodist", tags=["methodist_escalations"])
logger = logging.getLogger("api.methodist_escalations")


class EscalationItem(BaseModel):
    """Элемент списка эскалаций для методиста."""
    id: int
    created_at: datetime
    kind: str
    title: Optional[str] = None
    payload: dict
    read_at: Optional[datetime] = None


class EscalationListResponse(BaseModel):
    """Ответ /escalations/pending."""
    items: list[EscalationItem]
    count: int = Field(..., description="Длина items (≤ limit)")


async def _user_is_methodist(db: AsyncSession, user_id: int) -> bool:
    res = await db.execute(
        text(
            "SELECT 1 FROM user_roles ur "
            "JOIN roles r ON r.id = ur.role_id "
            "WHERE ur.user_id = :uid AND r.name = 'methodist' "
            "LIMIT 1"
        ),
        {"uid": user_id},
    )
    return res.fetchone() is not None


@router.get(
    "/escalations/pending",
    response_model=EscalationListResponse,
    status_code=status.HTTP_200_OK,
    summary="Список эскалаций для методиста (Phase Y-6)",
    responses={
        200: {"description": "Список (возможно пустой)"},
        401: {"description": "Не аутентифицирован"},
        403: {"description": "У пользователя нет роли methodist"},
    },
)
async def list_pending_escalations(
    since: Optional[datetime] = Query(
        None,
        description="Если указано — только эскалации с created_at >= since (ISO8601)",
    ),
    limit: int = Query(100, ge=1, le=500),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> EscalationListResponse:
    """Возвращает свежие эскалации `review_escalated` и `course_pending_review`
    для current_user (методиста). Используется TG_LMS methodist-поллером.
    """
    if not current_user.is_service:
        ok = await _user_is_methodist(db, current_user.id)
        if not ok:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Только для методиста")

    params: dict = {"uid": current_user.id, "limit": int(limit)}
    since_clause = ""
    if since is not None:
        since_clause = "AND n.modified_at >= :since "
        params["since"] = since

    res = await db.execute(
        text(
            "SELECT n.id, n.modified_at, n.kind, n.title, n.payload, n.read_at "
            "FROM notifications n "
            "WHERE n.user_id = :uid "
            "  AND n.kind IN ('review_escalated','course_pending_review') "
            f"  {since_clause}"  # nosec B608 — since_clause из закрытого набора (либо "", либо литерал с :since bind)
            "ORDER BY n.modified_at DESC "
            "LIMIT :limit"
        ),
        params,
    )
    rows = res.fetchall()
    items = [
        EscalationItem(
            id=int(r[0]),
            created_at=r[1],
            kind=str(r[2]),
            title=str(r[3]) if r[3] is not None else None,
            payload=dict(r[4]) if r[4] is not None else {},
            read_at=r[5],
        )
        for r in rows
    ]
    return EscalationListResponse(items=items, count=len(items))
