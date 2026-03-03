"""
Learning Engine V1, СЌС‚Р°Рї 3.8: API Р·Р°СЏРІРѕРє РЅР° РїРѕРјРѕС‰СЊ РґР»СЏ РїСЂРµРїРѕРґР°РІР°С‚РµР»СЏ/РјРµС‚РѕРґРёСЃС‚Р°.

GET /api/v1/teacher/help-requests вЂ” СЃРїРёСЃРѕРє Р·Р°СЏРІРѕРє
GET /api/v1/teacher/help-requests/{request_id} вЂ” РєР°СЂС‚РѕС‡РєР° Р·Р°СЏРІРєРё
POST /api/v1/teacher/help-requests/{request_id}/close вЂ” Р·Р°РєСЂС‹С‚СЊ Р·Р°СЏРІРєСѓ
POST /api/v1/teacher/help-requests/{request_id}/reply вЂ” РѕС‚РІРµС‚РёС‚СЊ СЃС‚СѓРґРµРЅС‚Сѓ
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
)
from app.services.teacher_queue_service import (
    claim_next_help_request,
    release_help_request_claim,
)

router = APIRouter(prefix="/teacher/help-requests", tags=["teacher_help_requests"])
logger = logging.getLogger("api.teacher_help_requests")


# ----- Р­С‚Р°Рї 3.9: claim-next (РјР°СЂС€СЂСѓС‚ РґРѕ /{request_id}, С‡С‚РѕР±С‹ "claim-next" РЅРµ Р·Р°С…РІР°С‚С‹РІР°Р»СЃСЏ РєР°Рє id) -----

@router.post(
    "/claim-next",
    response_model=HelpRequestClaimNextResponse,
    status_code=status.HTTP_200_OK,
    summary="Р’Р·СЏС‚СЊ СЃР»РµРґСѓСЋС‰РёР№ РѕС‚РєСЂС‹С‚С‹Р№ help-request (Р°С‚РѕРјР°СЂРЅС‹Р№ claim)",
    responses={
        200: {"description": "РљРµР№СЃ РІС‹РґР°РЅ РёР»Рё empty=true"},
        422: {"description": "РќРµРІР°Р»РёРґРЅС‹Рµ РїР°СЂР°РјРµС‚СЂС‹"},
    },
)
async def help_request_claim_next(
    body: HelpRequestClaimNextRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> HelpRequestClaimNextResponse:
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
    "",
    response_model=HelpRequestListResponse,
    summary="РЎРїРёСЃРѕРє Р·Р°СЏРІРѕРє РЅР° РїРѕРјРѕС‰СЊ (СЃ ACL)",
)
async def help_requests_list(
    teacher_id: int = Query(..., description="ID РїСЂРµРїРѕРґР°РІР°С‚РµР»СЏ/РјРµС‚РѕРґРёСЃС‚Р°"),
    status_filter: str = Query("open", description="open | closed | all", alias="status"),
    request_type_filter: str = Query("all", description="manual_help | blocked_limit | all", alias="request_type"),
    sort: str = Query("priority", description="priority | created_at | due_at (СЌС‚Р°Рї 3.9)", alias="sort"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> HelpRequestListResponse:
    if status_filter not in ("open", "closed", "all"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ open, closed РёР»Рё all",
        )
    if request_type_filter not in ("manual_help", "blocked_limit", "all"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="request_type РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ manual_help, blocked_limit РёР»Рё all",
        )
    if sort not in ("priority", "created_at", "due_at"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sort РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ priority, created_at РёР»Рё due_at",
        )
    items, total = await list_help_requests(
        db, teacher_id, status_filter, request_type_filter, limit, offset, sort=sort
    )
    return HelpRequestListResponse(
        items=[HelpRequestListItem(**it) for it in items],
        total=total,
    )


@router.get(
    "/{request_id}",
    response_model=HelpRequestDetailResponse,
    summary="РљР°СЂС‚РѕС‡РєР° Р·Р°СЏРІРєРё РЅР° РїРѕРјРѕС‰СЊ",
    responses={404: {"description": "Р—Р°СЏРІРєР° РЅРµ РЅР°Р№РґРµРЅР°"}, 403: {"description": "РќРµС‚ РґРѕСЃС‚СѓРїР°"}},
)
async def help_request_detail(
    request_id: int = Path(..., description="ID Р·Р°СЏРІРєРё"),
    teacher_id: int = Query(..., description="ID РїСЂРµРїРѕРґР°РІР°С‚РµР»СЏ/РјРµС‚РѕРґРёСЃС‚Р°"),
    db: AsyncSession = Depends(get_db),
) -> HelpRequestDetailResponse:
    detail, err = await get_help_request_detail(db, request_id, teacher_id)
    if err == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Р—Р°СЏРІРєР° РЅРµ РЅР°Р№РґРµРЅР°")
    if err == "forbidden" or detail is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="РќРµС‚ РґРѕСЃС‚СѓРїР° Рє Р·Р°СЏРІРєРµ")
    detail["history"] = [HelpRequestReplyItem(**h) for h in detail["history"]]
    return HelpRequestDetailResponse(**detail)


@router.post(
    "/{request_id}/close",
    response_model=HelpRequestCloseResponse,
    status_code=status.HTTP_200_OK,
    summary="Р—Р°РєСЂС‹С‚СЊ Р·Р°СЏРІРєСѓ (РёРґРµРјРїРѕС‚РµРЅС‚РЅРѕ)",
    responses={
        404: {"description": "Заявка не найдена"},
        403: {"description": "Нет доступа"},
        409: {"description": "Токен блокировки невалиден или просрочен"},
    },
)
async def help_request_close(
    request_id: int = Path(..., description="ID Р·Р°СЏРІРєРё"),
    body: HelpRequestCloseRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> HelpRequestCloseResponse:
    if not await help_request_exists(db, request_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Р—Р°СЏРІРєР° РЅРµ РЅР°Р№РґРµРЅР°")
    ok = await can_access_help_request(db, request_id, body.closed_by)
    if not ok:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="РќРµС‚ РґРѕСЃС‚СѓРїР° Рє Р·Р°СЏРІРєРµ")
    data, already, lock_err = await close_help_request(
        db, request_id, body.closed_by, body.resolution_comment, lock_token=body.lock_token
    )
    if lock_err == "lock_conflict":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="РўРѕРєРµРЅ Р±Р»РѕРєРёСЂРѕРІРєРё РЅРµРІР°Р»РёРґРµРЅ РёР»Рё РїСЂРѕСЃСЂРѕС‡РµРЅ",
        )
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Р—Р°СЏРІРєР° РЅРµ РЅР°Р№РґРµРЅР°")
    await db.commit()
    return HelpRequestCloseResponse(**data)


@router.post(
    "/{request_id}/release",
    response_model=HelpRequestReleaseResponse,
    status_code=status.HTTP_200_OK,
    summary="РћСЃРІРѕР±РѕРґРёС‚СЊ Р±Р»РѕРєРёСЂРѕРІРєСѓ Р·Р°СЏРІРєРё (СЌС‚Р°Рї 3.9)",
    responses={
        200: {"description": "released=true РёР»Рё РёРґРµРјРїРѕС‚РµРЅС‚РЅРѕ released=false"},
        404: {"description": "Р—Р°СЏРІРєР° РЅРµ РЅР°Р№РґРµРЅР°"},
        409: {"description": "РўРѕРєРµРЅ РЅРµ СЃРѕРІРїР°Р» РёР»Рё РєРµР№СЃ Сѓ РґСЂСѓРіРѕРіРѕ РїСЂРµРїРѕРґР°РІР°С‚РµР»СЏ"},
    },
)
async def help_request_release(
    request_id: int = Path(..., description="ID Р·Р°СЏРІРєРё"),
    body: HelpRequestReleaseRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> HelpRequestReleaseResponse:
    if not await help_request_exists(db, request_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Р—Р°СЏРІРєР° РЅРµ РЅР°Р№РґРµРЅР°")
    released, err = await release_help_request_claim(
        db, request_id, body.teacher_id, body.lock_token
    )
    if err == "forbidden":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="РўРѕРєРµРЅ Р±Р»РѕРєРёСЂРѕРІРєРё РЅРµ СЃРѕРІРїР°РґР°РµС‚ РёР»Рё Р·Р°СЏРІРєР° Р·Р°С…РІР°С‡РµРЅР° РґСЂСѓРіРёРј РїСЂРµРїРѕРґР°РІР°С‚РµР»РµРј",
        )
    await db.commit()
    return HelpRequestReleaseResponse(released=released)


@router.post(
    "/{request_id}/reply",
    response_model=HelpRequestReplyResponse,
    status_code=status.HTTP_200_OK,
    summary="РћС‚РІРµС‚РёС‚СЊ СЃС‚СѓРґРµРЅС‚Сѓ (СЃРѕРѕР±С‰РµРЅРёРµ РІ messages, РёРґРµРјРїРѕС‚РµРЅС‚РЅРѕ РїРѕ idempotency_key)",
    responses={
        404: {"description": "Р—Р°СЏРІРєР° РЅРµ РЅР°Р№РґРµРЅР°"},
        403: {"description": "РќРµС‚ РґРѕСЃС‚СѓРїР°"},
        409: {"description": "Р—Р°СЏРІРєР° СѓР¶Рµ Р·Р°РєСЂС‹С‚Р°, РѕС‚РІРµС‚ Р·Р°РїСЂРµС‰С‘РЅ"},
    },
)
async def help_request_reply(
    request_id: int = Path(..., description="ID Р·Р°СЏРІРєРё"),
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
        lock_token=body.lock_token,
    )
    if err == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Р—Р°СЏРІРєР° РЅРµ РЅР°Р№РґРµРЅР°")
    if err == "forbidden":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="РќРµС‚ РґРѕСЃС‚СѓРїР° Рє Р·Р°СЏРІРєРµ")
    if err == "closed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Р—Р°СЏРІРєР° СѓР¶Рµ Р·Р°РєСЂС‹С‚Р°. РћС‚РІРµС‚ РІ Р·Р°РєСЂС‹С‚СѓСЋ Р·Р°СЏРІРєСѓ Р·Р°РїСЂРµС‰С‘РЅ.",
        )
    if err == "lock_conflict":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="РўРѕРєРµРЅ Р±Р»РѕРєРёСЂРѕРІРєРё РЅРµРІР°Р»РёРґРµРЅ РёР»Рё РїСЂРѕСЃСЂРѕС‡РµРЅ",
        )
    await db.commit()
    return HelpRequestReplyResponse(**data)
