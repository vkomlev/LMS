"""
Learning Engine V1, этап 3.8: API заявок на помощь для преподавателя/методиста.

GET /api/v1/teacher/help-requests — список заявок
GET /api/v1/teacher/help-requests/{request_id} — карточка заявки
POST /api/v1/teacher/help-requests/{request_id}/close — закрыть заявку
POST /api/v1/teacher/help-requests/{request_id}/reply — ответить студенту
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Body, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.teacher_help_requests import (
    HelpRequestListResponse,
    HelpRequestListItem,
    HelpRequestDetailResponse,
    HelpRequestReplyItem,
    HelpRequestCloseRequest,
    HelpRequestCloseResponse,
    HelpRequestReplyRequest,
    HelpRequestReplyResponse,
)
from app.services.help_requests_service import (
    list_help_requests,
    get_help_request_detail,
    can_access_help_request,
    help_request_exists,
    close_help_request,
    reply_help_request,
)

router = APIRouter(prefix="/teacher/help-requests", tags=["teacher_help_requests"])
logger = logging.getLogger("api.teacher_help_requests")


@router.get(
    "",
    response_model=HelpRequestListResponse,
    summary="Список заявок на помощь (с ACL)",
)
async def help_requests_list(
    teacher_id: int = Query(..., description="ID преподавателя/методиста"),
    status_filter: str = Query("open", description="open | closed | all", alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> HelpRequestListResponse:
    if status_filter not in ("open", "closed", "all"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status должен быть open, closed или all",
        )
    items, total = await list_help_requests(db, teacher_id, status_filter, limit, offset)
    return HelpRequestListResponse(
        items=[HelpRequestListItem(**it) for it in items],
        total=total,
    )


@router.get(
    "/{request_id}",
    response_model=HelpRequestDetailResponse,
    summary="Карточка заявки на помощь",
    responses={404: {"description": "Заявка не найдена"}, 403: {"description": "Нет доступа"}},
)
async def help_request_detail(
    request_id: int = Path(..., description="ID заявки"),
    teacher_id: int = Query(..., description="ID преподавателя/методиста"),
    db: AsyncSession = Depends(get_db),
) -> HelpRequestDetailResponse:
    detail, err = await get_help_request_detail(db, request_id, teacher_id)
    if err == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Заявка не найдена")
    if err == "forbidden" or detail is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к заявке")
    detail["history"] = [HelpRequestReplyItem(**h) for h in detail["history"]]
    return HelpRequestDetailResponse(**detail)


@router.post(
    "/{request_id}/close",
    response_model=HelpRequestCloseResponse,
    status_code=status.HTTP_200_OK,
    summary="Закрыть заявку (идемпотентно)",
    responses={404: {"description": "Заявка не найдена"}, 403: {"description": "Нет доступа"}},
)
async def help_request_close(
    request_id: int = Path(..., description="ID заявки"),
    body: HelpRequestCloseRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> HelpRequestCloseResponse:
    if not await help_request_exists(db, request_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Заявка не найдена")
    ok = await can_access_help_request(db, request_id, body.closed_by)
    if not ok:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к заявке")
    data, already = await close_help_request(
        db, request_id, body.closed_by, body.resolution_comment
    )
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Заявка не найдена")
    await db.commit()
    return HelpRequestCloseResponse(**data)


@router.post(
    "/{request_id}/reply",
    response_model=HelpRequestReplyResponse,
    status_code=status.HTTP_200_OK,
    summary="Ответить студенту (сообщение в messages, идемпотентно по idempotency_key)",
    responses={
        404: {"description": "Заявка не найдена"},
        403: {"description": "Нет доступа"},
        409: {"description": "Заявка уже закрыта, ответ запрещён"},
    },
)
async def help_request_reply(
    request_id: int = Path(..., description="ID заявки"),
    body: HelpRequestReplyRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> HelpRequestReplyResponse:
    data, err = await reply_help_request(
        db,
        request_id,
        body.teacher_id,
        body.message,
        close_after_reply=body.close_after_reply,
        idempotency_key=body.idempotency_key,
    )
    if err == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Заявка не найдена")
    if err == "forbidden":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к заявке")
    if err == "closed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Заявка уже закрыта. Ответ в закрытую заявку запрещён.",
        )
    await db.commit()
    return HelpRequestReplyResponse(**data)
