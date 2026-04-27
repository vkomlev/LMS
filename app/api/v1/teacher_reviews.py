"""
Learning Engine V1, этап 3.9: claim-next и release для ручной проверки (manual review).

POST /api/v1/teacher/reviews/claim-next — взять следующий результат на проверку
POST /api/v1/teacher/reviews/{result_id}/release — освободить блокировку
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, get_current_user
from app.auth.current_user import CurrentUser
from app.schemas.teacher_next_modes import (
    ReviewClaimNextRequest,
    ReviewClaimNextResponse,
    ReviewClaimItem,
    ReviewReleaseRequest,
    ReviewReleaseResponse,
)
from app.services.teacher_queue_service import claim_next_review, release_review_claim

router = APIRouter(prefix="/teacher/reviews", tags=["teacher_reviews"])
logger = logging.getLogger("api.teacher_reviews")


@router.post(
    "/claim-next",
    response_model=ReviewClaimNextResponse,
    status_code=status.HTTP_200_OK,
    summary="Взять следующий результат на ручную проверку (атомарный claim)",
    responses={
        200: {"description": "Кейс выдан или empty=true"},
        422: {"description": "Невалидные параметры"},
    },
)
async def review_claim_next(
    body: ReviewClaimNextRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> ReviewClaimNextResponse:
    if not current_user.is_service and current_user.id != body.teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    item, lock_token, lock_expires_at = await claim_next_review(
        db,
        teacher_id=body.teacher_id,
        ttl_sec=body.ttl_sec,
        course_id=body.course_id,
        user_id=body.user_id,
        idempotency_key=body.idempotency_key,
    )
    await db.commit()
    if item is None:
        return ReviewClaimNextResponse(empty=True, item=None, lock_token=None, lock_expires_at=None)
    return ReviewClaimNextResponse(
        empty=False,
        item=ReviewClaimItem(**item),
        lock_token=lock_token,
        lock_expires_at=lock_expires_at,
    )


@router.post(
    "/{result_id}/release",
    response_model=ReviewReleaseResponse,
    status_code=status.HTTP_200_OK,
    summary="Освободить блокировку проверки (этап 3.9)",
    responses={
        200: {"description": "released=true или идемпотентно released=false"},
        404: {"description": "Результат не найден"},
        409: {"description": "Токен не совпал или кейс у другого преподавателя"},
    },
)
async def review_release(
    result_id: int = Path(..., description="ID результата (task_result)"),
    body: ReviewReleaseRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> ReviewReleaseResponse:
    if not current_user.is_service and current_user.id != body.teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    released, err = await release_review_claim(
        db, result_id, body.teacher_id, body.lock_token
    )
    if err == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Результат не найден")
    if err == "forbidden":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Токен блокировки не совпадает или проверка захвачена другим преподавателем",
        )
    await db.commit()
    return ReviewReleaseResponse(released=released)
