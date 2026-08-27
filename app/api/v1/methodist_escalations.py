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

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
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
    current_user: CurrentUser = Depends(require_role("methodist")),
    db: AsyncSession = Depends(get_async_db),
) -> EscalationListResponse:
    """Возвращает свежее из того, что требует внимания методиста, для
    current_user. Используется кабинетом методиста и TG_LMS methodist-поллером.

    Виды: `review_escalated` и `course_pending_review` (обе — про зависшие
    проверки) плюс `broken_media_links` (tsk-521 — проверка нашла ссылки на
    файлы, которых нет; ученик видит на их месте пустоту) и
    `help_request_escalated` (tsk-303, уровень 3 лестницы помощи — ученику не
    помог даже индивидуальный разбор с преподавателем; заявка остаётся
    открытой, методист закрывает её через
    `POST /teacher/help-requests/{id}/close`) и `schedule_slot_request`
    (tsk-674 фаза 3 — ученик не нашёл подходящего времени в расписании;
    заявка ждёт разбора в `GET /methodist/schedule-slot-requests`).

    tsk-298: проверка НАЛИЧИЯ роли `methodist` централизована в
    `require_role("methodist")` (service-token — bypass, как и раньше);
    поведение эндпоинта не изменилось.
    """
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
            "  AND n.kind IN ('review_escalated','course_pending_review','broken_media_links',"
            "'help_request_escalated','schedule_slot_request') "
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
