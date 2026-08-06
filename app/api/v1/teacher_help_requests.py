"""
Learning Engine V1, этап 3.8: API заявок на помощь для преподавателя/методиста.

GET /api/v1/teacher/help-requests — список заявок
GET /api/v1/teacher/help-requests/{request_id} — карточка заявки
POST /api/v1/teacher/help-requests/{request_id}/close — закрыть заявку
POST /api/v1/teacher/help-requests/{request_id}/reply — ответить студенту
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Body, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, get_current_user
from app.auth.current_user import CurrentUser
from app.schemas.teacher_help_requests import (
    HelpRequestListResponse,
    HelpRequestListItem,
    HelpRequestDetailResponse,
    HelpRequestReplyItem,
    HelpRequestCloseRequest,
    HelpRequestCloseResponse,
    HelpRequestReplyRequest,
    HelpRequestReplyResponse,
    HelpRequestPendingCountResponse,
    WebinarLinkRequest,
    WebinarLinkResponse,
    ReopenKpiItem,
    ReopenKpiResponse,
)
from app.schemas.teacher_next_modes import (
    HelpRequestClaimNextRequest,
    HelpRequestClaimNextResponse,
    HelpRequestClaimItem,
    HelpRequestReleaseRequest,
    HelpRequestReleaseResponse,
)
from app.services.help_requests_service import (
    list_help_requests,
    get_help_request_detail,
    can_access_help_request,
    help_request_exists,
    close_help_request,
    reply_help_request,
    set_webinar_link,
    get_help_requests_pending_count,
    get_reopen_kpi,
)
from app.services import roles_service
from app.services.teacher_queue_service import (
    claim_next_help_request,
    release_help_request_claim,
)

router = APIRouter(prefix="/teacher/help-requests", tags=["teacher_help_requests"])
logger = logging.getLogger("api.teacher_help_requests")


# ----- Этап 3.9: claim-next (маршрут до /{request_id}, чтобы "claim-next" не захватывался как id) -----

@router.post(
    "/claim-next",
    response_model=HelpRequestClaimNextResponse,
    status_code=status.HTTP_200_OK,
    summary="Взять следующий открытый help-request (атомарный claim)",
    responses={
        200: {"description": "Кейс выдан или empty=true"},
        422: {"description": "Невалидные параметры"},
    },
)
async def help_request_claim_next(
    body: HelpRequestClaimNextRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> HelpRequestClaimNextResponse:
    if not current_user.is_service and current_user.id != body.teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    item, lock_token, lock_expires_at = await claim_next_help_request(
        db,
        teacher_id=body.teacher_id,
        request_type=body.request_type,
        ttl_sec=body.ttl_sec,
        course_id=body.course_id,
        idempotency_key=body.idempotency_key,
    )
    await db.commit()
    if item is None:
        return HelpRequestClaimNextResponse(empty=True, item=None, lock_token=None, lock_expires_at=None)
    return HelpRequestClaimNextResponse(
        empty=False,
        item=HelpRequestClaimItem(**item),
        lock_token=lock_token,
        lock_expires_at=lock_expires_at,
    )


@router.get(
    "/pending-count",
    response_model=HelpRequestPendingCountResponse,
    summary="Количество открытых заявок помощи, назначенных на преподавателя (tsk-348)",
)
async def help_requests_pending_count(
    teacher_id: int = Query(..., description="ID преподавателя"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> HelpRequestPendingCountResponse:
    if not current_user.is_service and current_user.id != teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    count, oldest = await get_help_requests_pending_count(db, teacher_id)
    return HelpRequestPendingCountResponse(count=count, oldest_created_at=oldest)


@router.get(
    "/kpi/reopens",
    response_model=ReopenKpiResponse,
    summary="Возвраты заявок: свой показатель или сводка по всем (tsk-303)",
    responses={
        200: {"description": "Сводка возвратов"},
        403: {"description": "Чужой teacher_id без роли методиста/админа"},
    },
)
async def help_request_reopen_kpi(
    teacher_id: Optional[int] = Query(
        None,
        description="ID преподавателя. Не задан — сводка по всем (только методист/админ).",
    ),
    since: Optional[datetime] = Query(
        None, description="Считать возвраты с этого момента (ISO8601)"
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> ReopenKpiResponse:
    """Две поверхности одного агрегата (решение оператора: KPI видят оба).

    Преподаватель смотрит СВОЙ показатель — для самоконтроля. Методист и админ
    видят сводку по всем — это оценка преподавателей, поэтому чужой показатель
    без такой роли не отдаётся. Считает один и тот же
    `help_requests_service.get_reopen_kpi`, чтобы две панели не разъехались в
    цифрах.
    """
    is_privileged = current_user.is_service or bool(
        {"methodist", "admin"}
        & set(await roles_service.get_user_role_names(db, current_user.id))
    )
    if teacher_id is None:
        if not is_privileged:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Сводка по всем преподавателям доступна методисту или админу",
            )
    elif not is_privileged and current_user.id != teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")

    items = await get_reopen_kpi(db, teacher_id=teacher_id, since=since)
    return ReopenKpiResponse(
        items=[ReopenKpiItem(**it) for it in items],
        total_reopens=sum(int(it["reopens"]) for it in items),
        since=since,
    )


@router.get(
    "",
    response_model=HelpRequestListResponse,
    summary="Список заявок на помощь (с ACL)",
)
async def help_requests_list(
    teacher_id: int = Query(..., description="ID преподавателя/методиста"),
    status_filter: str = Query("open", description="open | closed | all", alias="status"),
    request_type_filter: str = Query(
        "all",
        description="manual_help | blocked_limit | individual_review | all",
        alias="request_type",
    ),
    sort: str = Query("priority", description="priority | created_at | due_at (этап 3.9)", alias="sort"),
    overdue: bool = Query(False, description="true — только просроченные (due_at < now), ортогонально типу (tsk-312)"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> HelpRequestListResponse:
    if not current_user.is_service and current_user.id != teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    if status_filter not in ("open", "closed", "all"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status должен быть open, closed или all",
        )
    if request_type_filter not in ("manual_help", "blocked_limit", "individual_review", "all"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "request_type должен быть manual_help, blocked_limit, "
                "individual_review или all"
            ),
        )
    if sort not in ("priority", "created_at", "due_at"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sort должен быть priority, created_at или due_at",
        )
    items, total = await list_help_requests(
        db, teacher_id, status_filter, request_type_filter, limit, offset, sort=sort, overdue=overdue
    )
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
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> HelpRequestDetailResponse:
    if not current_user.is_service and current_user.id != teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
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
    responses={
        404: {"description": "Заявка не найдена"},
        403: {"description": "Нет доступа"},
        409: {"description": "Токен блокировки невалиден или просрочен"},
    },
)
async def help_request_close(
    request_id: int = Path(..., description="ID заявки"),
    body: HelpRequestCloseRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> HelpRequestCloseResponse:
    if not current_user.is_service and current_user.id != body.closed_by:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    if not await help_request_exists(db, request_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Заявка не найдена")
    ok = await can_access_help_request(db, request_id, body.closed_by)
    if not ok:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к заявке")
    data, already, lock_err = await close_help_request(
        db, request_id, body.closed_by, body.resolution_comment, lock_token=body.lock_token
    )
    if lock_err == "lock_conflict":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Токен блокировки невалиден или просрочен",
        )
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Заявка не найдена")
    await db.commit()
    return HelpRequestCloseResponse(**data)


@router.post(
    "/{request_id}/release",
    response_model=HelpRequestReleaseResponse,
    status_code=status.HTTP_200_OK,
    summary="Освободить блокировку заявки (этап 3.9)",
    responses={
        200: {"description": "released=true или идемпотентно released=false"},
        404: {"description": "Заявка не найдена"},
        409: {"description": "Токен не совпал или кейс у другого преподавателя"},
    },
)
async def help_request_release(
    request_id: int = Path(..., description="ID заявки"),
    body: HelpRequestReleaseRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> HelpRequestReleaseResponse:
    if not current_user.is_service and current_user.id != body.teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    if not await help_request_exists(db, request_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Заявка не найдена")
    released, err = await release_help_request_claim(
        db, request_id, body.teacher_id, body.lock_token
    )
    if err == "forbidden":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Токен блокировки не совпадает или заявка захвачена другим преподавателем",
        )
    await db.commit()
    return HelpRequestReleaseResponse(released=released)


@router.post(
    "/{request_id}/webinar-link",
    response_model=WebinarLinkResponse,
    status_code=status.HTTP_200_OK,
    summary="Прислать ученику ссылку на индивидуальный разбор (tsk-303, уровень 2)",
    responses={
        404: {"description": "Заявка не найдена"},
        403: {"description": "Нет доступа"},
        409: {"description": "Заявка не того типа, закрыта или токен блокировки невалиден"},
        422: {"description": "Ссылка пустая или не http/https"},
    },
)
async def help_request_webinar_link(
    request_id: int = Path(..., description="ID заявки"),
    body: WebinarLinkRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> WebinarLinkResponse:
    """Заявку не закрывает: разбор впереди, закроет её оценка ученика."""
    if not current_user.is_service and current_user.id != body.teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    data, err = await set_webinar_link(
        db, request_id, body.teacher_id, body.webinar_link, lock_token=body.lock_token
    )
    if err == "not_found":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    if err == "forbidden":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к заявке")
    if err == "lock_conflict":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Токен блокировки невалиден или просрочен"
        )
    if err == "wrong_type":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Ссылка на разбор доступна только по заявке на индивидуальный разбор",
        )
    if err == "not_open":
        raise HTTPException(status.HTTP_409_CONFLICT, "Заявка закрыта")
    await db.commit()
    logger.info("tsk-303 webinar-link: request_id=%s teacher_id=%s", request_id, body.teacher_id)
    return WebinarLinkResponse(**data)


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
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> HelpRequestReplyResponse:
    if not current_user.is_service and current_user.id != body.teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    data, err = await reply_help_request(
        db,
        request_id,
        body.teacher_id,
        body.message,
        close_after_reply=body.close_after_reply,
        idempotency_key=body.idempotency_key,
        lock_token=body.lock_token,
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
    if err == "lock_conflict":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Токен блокировки невалиден или просрочен",
        )
    await db.commit()
    return HelpRequestReplyResponse(**data)
