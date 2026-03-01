from __future__ import annotations

from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Body, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.api.deps import get_db
from app.schemas.task_results import TaskResultRead, TaskResultUpdate, TaskResultManualCheckRequest
from app.services.task_results_service import TaskResultsService


router = APIRouter(tags=["task_results"])

task_results_service = TaskResultsService()


@router.get(
    "/task-results/by-user/{user_id}",
    response_model=List[TaskResultRead],
    summary="РџРѕР»СѓС‡РёС‚СЊ СЂРµР·СѓР»СЊС‚Р°С‚С‹ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ",
    responses={
        200: {
            "description": "РЎРїРёСЃРѕРє СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "id": 1,
                            "attempt_id": 1,
                            "task_id": 1,
                            "score": 10,
                            "max_score": 10,
                            "is_correct": True,
                            "feedback": [],
                            "created_at": "2026-01-17T12:00:00Z",
                        }
                    ]
                }
            }
        },
    },
)
async def get_task_results_by_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000, description="РњР°РєСЃРёРјСѓРј Р·Р°РїРёСЃРµР№ РЅР° СЃС‚СЂР°РЅРёС†Рµ"),
    offset: int = Query(0, ge=0, description="РЎРјРµС‰РµРЅРёРµ"),
) -> List[TaskResultRead]:
    """
    РџРѕР»СѓС‡РёС‚СЊ СЃРїРёСЃРѕРє СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ РІС‹РїРѕР»РЅРµРЅРёСЏ Р·Р°РґР°РЅРёР№ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ СЃ РїР°РіРёРЅР°С†РёРµР№.

    Args:
        user_id: ID РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ.
        limit: РњР°РєСЃРёРјСѓРј Р·Р°РїРёСЃРµР№ РЅР° СЃС‚СЂР°РЅРёС†Рµ (1-1000).
        offset: РЎРјРµС‰РµРЅРёРµ РґР»СЏ РїР°РіРёРЅР°С†РёРё.

    Returns:
        РЎРїРёСЃРѕРє СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ.
    """
    results, total = await task_results_service.get_by_user(
        db,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )
    return [TaskResultRead.model_validate(result) for result in results]


@router.get(
    "/task-results/by-task/{task_id}",
    response_model=List[TaskResultRead],
    summary="РџРѕР»СѓС‡РёС‚СЊ СЂРµР·СѓР»СЊС‚Р°С‚С‹ РїРѕ Р·Р°РґР°С‡Рµ",
    responses={
        200: {
            "description": "РЎРїРёСЃРѕРє СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ РїРѕ Р·Р°РґР°С‡Рµ",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "id": 1,
                            "attempt_id": 1,
                            "task_id": 1,
                            "score": 10,
                            "max_score": 10,
                            "is_correct": True,
                            "feedback": [],
                            "created_at": "2026-01-17T12:00:00Z",
                        }
                    ]
                }
            }
        },
        404: {
            "description": "Р—Р°РґР°С‡Р° РЅРµ РЅР°Р№РґРµРЅР°",
        },
    },
)
async def get_task_results_by_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000, description="РњР°РєСЃРёРјСѓРј Р·Р°РїРёСЃРµР№ РЅР° СЃС‚СЂР°РЅРёС†Рµ"),
    offset: int = Query(0, ge=0, description="РЎРјРµС‰РµРЅРёРµ"),
) -> List[TaskResultRead]:
    """
    РџРѕР»СѓС‡РёС‚СЊ СЃРїРёСЃРѕРє СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ РІС‹РїРѕР»РЅРµРЅРёСЏ РєРѕРЅРєСЂРµС‚РЅРѕР№ Р·Р°РґР°С‡Рё СЃ РїР°РіРёРЅР°С†РёРµР№.

    Args:
        task_id: ID Р·Р°РґР°С‡Рё.
        limit: РњР°РєСЃРёРјСѓРј Р·Р°РїРёСЃРµР№ РЅР° СЃС‚СЂР°РЅРёС†Рµ (1-1000).
        offset: РЎРјРµС‰РµРЅРёРµ РґР»СЏ РїР°РіРёРЅР°С†РёРё.

    Returns:
        РЎРїРёСЃРѕРє СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ РїРѕ Р·Р°РґР°С‡Рµ.
    """
    results, total = await task_results_service.get_by_task(
        db,
        task_id=task_id,
        limit=limit,
        offset=offset,
    )
    return [TaskResultRead.model_validate(result) for result in results]


@router.get(
    "/task-results/by-attempt/{attempt_id}",
    response_model=List[TaskResultRead],
    summary="РџРѕР»СѓС‡РёС‚СЊ СЂРµР·СѓР»СЊС‚Р°С‚С‹ РїРѕРїС‹С‚РєРё",
    responses={
        200: {
            "description": "РЎРїРёСЃРѕРє СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ РїРѕРїС‹С‚РєРё",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "id": 1,
                            "attempt_id": 1,
                            "task_id": 1,
                            "score": 10,
                            "max_score": 10,
                            "is_correct": True,
                            "feedback": [],
                            "created_at": "2026-01-17T12:00:00Z",
                        }
                    ]
                }
            }
        },
        404: {
            "description": "РџРѕРїС‹С‚РєР° РЅРµ РЅР°Р№РґРµРЅР°",
        },
    },
)
async def get_task_results_by_attempt(
    attempt_id: int,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000, description="РњР°РєСЃРёРјСѓРј Р·Р°РїРёСЃРµР№ РЅР° СЃС‚СЂР°РЅРёС†Рµ"),
    offset: int = Query(0, ge=0, description="РЎРјРµС‰РµРЅРёРµ"),
) -> List[TaskResultRead]:
    """
    РџРѕР»СѓС‡РёС‚СЊ СЃРїРёСЃРѕРє СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ РІС‹РїРѕР»РЅРµРЅРёСЏ Р·Р°РґР°РЅРёР№ РІ СЂР°РјРєР°С… РєРѕРЅРєСЂРµС‚РЅРѕР№ РїРѕРїС‹С‚РєРё СЃ РїР°РіРёРЅР°С†РёРµР№.

    Args:
        attempt_id: ID РїРѕРїС‹С‚РєРё.
        limit: РњР°РєСЃРёРјСѓРј Р·Р°РїРёСЃРµР№ РЅР° СЃС‚СЂР°РЅРёС†Рµ (1-1000).
        offset: РЎРјРµС‰РµРЅРёРµ РґР»СЏ РїР°РіРёРЅР°С†РёРё.

    Returns:
        РЎРїРёСЃРѕРє СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ РїРѕРїС‹С‚РєРё.
    """
    results, total = await task_results_service.get_by_attempt(
        db,
        attempt_id=attempt_id,
        limit=limit,
        offset=offset,
    )
    return [TaskResultRead.model_validate(result) for result in results]


@router.post(
    "/task-results/{result_id}/manual-check",
    response_model=TaskResultRead,
    summary="Р СѓС‡РЅР°СЏ РґРѕРѕС†РµРЅРєР° СЂРµР·СѓР»СЊС‚Р°С‚Р°",
    responses={
        200: {
            "description": "Р РµР·СѓР»СЊС‚Р°С‚ СѓСЃРїРµС€РЅРѕ РѕР±РЅРѕРІР»РµРЅ",
            "content": {
                "application/json": {
                    "example": {
                        "id": 1,
                        "attempt_id": 1,
                        "task_id": 1,
                        "score": 15,
                        "max_score": 20,
                        "is_correct": False,
                        "checked_at": "2026-01-17T12:00:00Z",
                        "checked_by": 2,
                        "user_id": 1,
                        "submitted_at": "2026-01-17T11:00:00Z",
                    }
                }
            }
        },
        400: {
            "description": "РќРµРІРµСЂРЅС‹Рµ РїР°СЂР°РјРµС‚СЂС‹ Р·Р°РїСЂРѕСЃР°",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "score РЅРµ РјРѕР¶РµС‚ Р±С‹С‚СЊ Р±РѕР»СЊС€Рµ max_score"
                    }
                }
            }
        },
        404: {
            "description": "Р РµР·СѓР»СЊС‚Р°С‚ РЅРµ РЅР°Р№РґРµРЅ",
        },
        409: {
            "description": "Токен блокировки невалиден или просрочен",
        },
    },
)
async def manual_check_task_result(
    result_id: int,
    payload: TaskResultManualCheckRequest = Body(
        ...,
        description="РџР°СЂР°РјРµС‚СЂС‹ СЂСѓС‡РЅРѕР№ РїСЂРѕРІРµСЂРєРё",
        examples=[
            {
                "summary": "Р СѓС‡РЅР°СЏ РїСЂРѕРІРµСЂРєР° СЃ РЅРѕРІС‹Рј Р±Р°Р»Р»РѕРј",
                "value": {
                    "score": 15,
                    "checked_by": 2,
                }
            },
            {
                "summary": "Р СѓС‡РЅР°СЏ РїСЂРѕРІРµСЂРєР° СЃ РѕР±РЅРѕРІР»РµРЅРёРµРј is_correct",
                "value": {
                    "score": 10,
                    "is_correct": True,
                    "checked_by": 2,
                }
            },
        ],
    ),
    db: AsyncSession = Depends(get_db),
) -> TaskResultRead:
    """
    Р’С‹РїРѕР»РЅРёС‚СЊ СЂСѓС‡РЅСѓСЋ РґРѕРѕС†РµРЅРєСѓ СЂРµР·СѓР»СЊС‚Р°С‚Р° РІС‹РїРѕР»РЅРµРЅРёСЏ Р·Р°РґР°С‡Рё.
    
    РџРѕР·РІРѕР»СЏРµС‚ РїСЂРµРїРѕРґР°РІР°С‚РµР»СЋ РёР»Рё Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂСѓ РёР·РјРµРЅРёС‚СЊ РѕС†РµРЅРєСѓ,
    СѓСЃС‚Р°РЅРѕРІР»РµРЅРЅСѓСЋ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРѕР№ РїСЂРѕРІРµСЂРєРѕР№, РёР»Рё РїСЂРѕРІРµСЂРёС‚СЊ Р·Р°РґР°С‡Сѓ,
    С‚СЂРµР±СѓСЋС‰СѓСЋ СЂСѓС‡РЅРѕР№ РїСЂРѕРІРµСЂРєРё.
    
    Args:
        result_id: ID СЂРµР·СѓР»СЊС‚Р°С‚Р° РґР»СЏ РїСЂРѕРІРµСЂРєРё.
        payload: РџР°СЂР°РјРµС‚СЂС‹ РїСЂРѕРІРµСЂРєРё:
            - score: РќРѕРІС‹Р№ Р±Р°Р»Р» (РѕР±СЏР·Р°С‚РµР»СЊРЅРѕ)
            - checked_by: ID РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ, РІС‹РїРѕР»РЅСЏСЋС‰РµРіРѕ РїСЂРѕРІРµСЂРєСѓ (РѕР±СЏР·Р°С‚РµР»СЊРЅРѕ)
            - is_correct: Р¤Р»Р°Рі РїСЂР°РІРёР»СЊРЅРѕСЃС‚Рё РѕС‚РІРµС‚Р° (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
            - metrics: Р”РѕРїРѕР»РЅРёС‚РµР»СЊРЅС‹Рµ РјРµС‚СЂРёРєРё (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    
    Returns:
        РћР±РЅРѕРІР»РµРЅРЅС‹Р№ СЂРµР·СѓР»СЊС‚Р°С‚.
    """
    payload_data = payload.model_dump(exclude_unset=True)
    score = payload_data.get("score")
    checked_by = payload_data.get("checked_by")
    
    if score is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="РџР°СЂР°РјРµС‚СЂ 'score' РѕР±СЏР·Р°С‚РµР»РµРЅ",
        )
    
    if checked_by is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="РџР°СЂР°РјРµС‚СЂ 'checked_by' РѕР±СЏР·Р°С‚РµР»РµРЅ",
        )
    
    # РџРѕР»СѓС‡Р°РµРј СЂРµР·СѓР»СЊС‚Р°С‚
    result = await task_results_service.get_by_id(db, result_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Р РµР·СѓР»СЊС‚Р°С‚ СЃ ID {result_id} РЅРµ РЅР°Р№РґРµРЅ",
        )

    # Р­С‚Р°Рї 3.9: РѕРїС†РёРѕРЅР°Р»СЊРЅР°СЏ РїСЂРѕРІРµСЂРєР° lock_token РґР»СЏ claim (РЅРѕСЂРјР°Р»РёР·Р°С†РёСЏ timezone вЂ” P1 fix)
    lock_token = payload_data.get("lock_token")
    if lock_token:
        from datetime import timezone
        now = datetime.now(timezone.utc)
        expires_at = getattr(result, "review_claim_expires_at", None)
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if (
            getattr(result, "review_claimed_by", None) != checked_by
            or getattr(result, "review_claim_token", None) != lock_token
            or not getattr(result, "review_claim_expires_at", None)
            or (expires_at is not None and expires_at < now)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="РўРѕРєРµРЅ Р±Р»РѕРєРёСЂРѕРІРєРё РЅРµРІР°Р»РёРґРµРЅ РёР»Рё РїСЂРѕСЃСЂРѕС‡РµРЅ",
            )
    
    # РџСЂРѕРІРµСЂСЏРµРј, С‡С‚Рѕ score РЅРµ РїСЂРµРІС‹С€Р°РµС‚ max_score
    max_score = result.max_score or 0
    if max_score > 0 and score > max_score:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"score ({score}) РЅРµ РјРѕР¶РµС‚ Р±С‹С‚СЊ Р±РѕР»СЊС€Рµ max_score ({max_score})",
        )
    
    if score < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="score РЅРµ РјРѕР¶РµС‚ Р±С‹С‚СЊ РѕС‚СЂРёС†Р°С‚РµР»СЊРЅС‹Рј",
        )
    
    # РћР±РЅРѕРІР»СЏРµРј СЂРµР·СѓР»СЊС‚Р°С‚; СЌС‚Р°Рї 3.9: СЃР±СЂР°СЃС‹РІР°РµРј claim РїРѕСЃР»Рµ РїСЂРѕРІРµСЂРєРё
    update_data = TaskResultUpdate(
        score=score,
        checked_by=checked_by,
        checked_at=datetime.now(),
        is_correct=payload_data.get("is_correct"),
        metrics=payload_data.get("metrics"),
    )
    update_dict = update_data.model_dump(exclude_unset=True)
    update_dict["review_claimed_by"] = None
    update_dict["review_claim_token"] = None
    update_dict["review_claim_expires_at"] = None

    # BaseService.update РѕР¶РёРґР°РµС‚ РѕР±СЉРµРєС‚ Р‘Р” Рё dict
    updated_result = await task_results_service.update(db, result, update_dict)
    return TaskResultRead.model_validate(updated_result)


@router.get(
    "/task-results/stats/by-task/{task_id}",
    summary="РЎС‚Р°С‚РёСЃС‚РёРєР° РїРѕ Р·Р°РґР°С‡Рµ",
    responses={
        200: {
            "description": "РЎС‚Р°С‚РёСЃС‚РёРєР° РїРѕ Р·Р°РґР°С‡Рµ",
            "content": {
                "application/json": {
                    "example": {
                        "task_id": 1,
                        "total_attempts": 50,
                        "average_score": 7.5,
                        "correct_percentage": 60.0,
                        "min_score": 0,
                        "max_score": 10,
                        "score_distribution": {},
                    }
                }
            }
        },
    },
)
async def get_task_stats(
    task_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    РџРѕР»СѓС‡РёС‚СЊ СЃС‚Р°С‚РёСЃС‚РёРєСѓ РїРѕ Р·Р°РґР°С‡Рµ.
    
    Р’РѕР·РІСЂР°С‰Р°РµС‚:
    - total_attempts: РћР±С‰РµРµ РєРѕР»РёС‡РµСЃС‚РІРѕ РїРѕРїС‹С‚РѕРє
    - average_score: РЎСЂРµРґРЅРёР№ Р±Р°Р»Р»
    - correct_percentage: РџСЂРѕС†РµРЅС‚ РїСЂР°РІРёР»СЊРЅС‹С… РѕС‚РІРµС‚РѕРІ
    - min_score, max_score: РњРёРЅРёРјР°Р»СЊРЅС‹Р№ Рё РјР°РєСЃРёРјР°Р»СЊРЅС‹Р№ Р±Р°Р»Р»С‹
    - score_distribution: Р Р°СЃРїСЂРµРґРµР»РµРЅРёРµ Р±Р°Р»Р»РѕРІ
    """
    return await task_results_service.get_stats_by_task(db, task_id)


@router.get(
    "/task-results/stats/by-course/{course_id}",
    summary="РЎС‚Р°С‚РёСЃС‚РёРєР° РїРѕ РєСѓСЂСЃСѓ",
    responses={
        200: {
            "description": "РЎС‚Р°С‚РёСЃС‚РёРєР° РїРѕ РєСѓСЂСЃСѓ",
            "content": {
                "application/json": {
                    "example": {
                        "course_id": 1,
                        "total_attempts": 200,
                        "average_score": 8.2,
                        "correct_percentage": 65.0,
                        "tasks_count": 10,
                    }
                }
            }
        },
    },
)
async def get_course_stats(
    course_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    РџРѕР»СѓС‡РёС‚СЊ СЃС‚Р°С‚РёСЃС‚РёРєСѓ РїРѕ РєСѓСЂСЃСѓ.
    
    Р’РѕР·РІСЂР°С‰Р°РµС‚ Р°РіСЂРµРіРёСЂРѕРІР°РЅРЅСѓСЋ СЃС‚Р°С‚РёСЃС‚РёРєСѓ РїРѕ РІСЃРµРј Р·Р°РґР°С‡Р°Рј РєСѓСЂСЃР°:
    - total_attempts: РћР±С‰РµРµ РєРѕР»РёС‡РµСЃС‚РІРѕ РїРѕРїС‹С‚РѕРє
    - average_score: РЎСЂРµРґРЅРёР№ Р±Р°Р»Р»
    - correct_percentage: РџСЂРѕС†РµРЅС‚ РїСЂР°РІРёР»СЊРЅС‹С… РѕС‚РІРµС‚РѕРІ
    - tasks_count: РљРѕР»РёС‡РµСЃС‚РІРѕ Р·Р°РґР°С‡ РІ РєСѓСЂСЃРµ
    """
    return await task_results_service.get_stats_by_course(db, course_id)


@router.get(
    "/task-results/stats/by-user/{user_id}",
    summary="РЎС‚Р°С‚РёСЃС‚РёРєР° РїРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ",
    responses={
        200: {
            "description": "РЎС‚Р°С‚РёСЃС‚РёРєР° РїРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ",
            "content": {
                "application/json": {
                    "example": {
                        "user_id": 1,
                        "total_attempts": 30,
                        "average_score": 7.8,
                        "correct_percentage": 70.0,
                        "total_score": 234,
                        "total_max_score": 300,
                        "completion_percentage": 78.0,
                    }
                }
            }
        },
    },
)
async def get_user_stats(
    user_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    РџРѕР»СѓС‡РёС‚СЊ СЃС‚Р°С‚РёСЃС‚РёРєСѓ РїРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ.
    
    Р’РѕР·РІСЂР°С‰Р°РµС‚:
    - total_attempts: РћР±С‰РµРµ РєРѕР»РёС‡РµСЃС‚РІРѕ РїРѕРїС‹С‚РѕРє
    - average_score: РЎСЂРµРґРЅРёР№ Р±Р°Р»Р»
    - correct_percentage: РџСЂРѕС†РµРЅС‚ РїСЂР°РІРёР»СЊРЅС‹С… РѕС‚РІРµС‚РѕРІ
    - total_score: РЎСѓРјРјР° РІСЃРµС… Р±Р°Р»Р»РѕРІ
    - total_max_score: РЎСѓРјРјР° РјР°РєСЃРёРјР°Р»СЊРЅС‹С… Р±Р°Р»Р»РѕРІ
    - completion_percentage: РџСЂРѕС†РµРЅС‚ РІС‹РїРѕР»РЅРµРЅРёСЏ (total_score / total_max_score * 100)
    """
    return await task_results_service.get_stats_by_user(db, user_id)


@router.get(
    "/task-results/by-pending-review",
    response_model=List[TaskResultRead],
    summary="РџРѕР»СѓС‡РёС‚СЊ СЂРµР·СѓР»СЊС‚Р°С‚С‹ Р·Р°РґР°РЅРёР№, С‚СЂРµР±СѓСЋС‰РёС… СЂСѓС‡РЅРѕР№ РїСЂРѕРІРµСЂРєРё",
    responses={
        200: {
            "description": "РЎРїРёСЃРѕРє СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ, С‚СЂРµР±СѓСЋС‰РёС… РїСЂРѕРІРµСЂРєРё",
        },
    },
)
async def get_pending_review_results(
    course_id: Optional[int] = Query(None, description="Р¤РёР»СЊС‚СЂ РїРѕ РєСѓСЂСЃСѓ"),
    user_id: Optional[int] = Query(None, description="Р¤РёР»СЊС‚СЂ РїРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ"),
    limit: int = Query(50, ge=1, le=1000, description="РњР°РєСЃРёРјСѓРј Р·Р°РїРёСЃРµР№ РЅР° СЃС‚СЂР°РЅРёС†Рµ"),
    offset: int = Query(0, ge=0, description="РЎРјРµС‰РµРЅРёРµ"),
    db: AsyncSession = Depends(get_db),
) -> List[TaskResultRead]:
    """
    РџРѕР»СѓС‡РёС‚СЊ СЃРїРёСЃРѕРє СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ Р·Р°РґР°РЅРёР№, С‚СЂРµР±СѓСЋС‰РёС… СЂСѓС‡РЅРѕР№ РїСЂРѕРІРµСЂРєРё.
    
    Р’РѕР·РІСЂР°С‰Р°РµС‚ СЂРµР·СѓР»СЊС‚Р°С‚С‹, РіРґРµ:
    - checked_at = null (РµС‰Рµ РЅРµ РїСЂРѕРІРµСЂРµРЅС‹)
    
    Args:
        course_id: РћРїС†РёРѕРЅР°Р»СЊРЅС‹Р№ С„РёР»СЊС‚СЂ РїРѕ РєСѓСЂСЃСѓ
        user_id: РћРїС†РёРѕРЅР°Р»СЊРЅС‹Р№ С„РёР»СЊС‚СЂ РїРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ
        limit: РњР°РєСЃРёРјСѓРј Р·Р°РїРёСЃРµР№ РЅР° СЃС‚СЂР°РЅРёС†Рµ (1-1000)
        offset: РЎРјРµС‰РµРЅРёРµ РґР»СЏ РїР°РіРёРЅР°С†РёРё
    
    Returns:
        РЎРїРёСЃРѕРє СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ, С‚СЂРµР±СѓСЋС‰РёС… РїСЂРѕРІРµСЂРєРё
    """
    from datetime import timezone
    from sqlalchemy import select, and_, or_
    from app.models.task_results import TaskResults
    from app.models.tasks import Tasks

    now = datetime.now(timezone.utc)
    # Р‘Р°Р·РѕРІРѕРµ СѓСЃР»РѕРІРёРµ: СЂРµР·СѓР»СЊС‚Р°С‚С‹ РЅРµ РїСЂРѕРІРµСЂРµРЅС‹; СЌС‚Р°Рї 3.9: РЅРµ РїРѕРєР°Р·С‹РІР°С‚СЊ Р·Р°С…РІР°С‡РµРЅРЅС‹Рµ РїРѕ TTL
    conditions = [
        TaskResults.checked_at.is_(None),
        or_(
            TaskResults.review_claim_expires_at.is_(None),
            TaskResults.review_claim_expires_at < now,
        ),
    ]

    # Р”РѕРїРѕР»РЅРёС‚РµР»СЊРЅС‹Рµ С„РёР»СЊС‚СЂС‹
    if course_id is not None:
        # РќСѓР¶РЅРѕ РїСЂРёСЃРѕРµРґРёРЅРёС‚СЊ tasks РґР»СЏ С„РёР»СЊС‚СЂР°С†РёРё РїРѕ course_id
        conditions.append(Tasks.course_id == course_id)
    
    if user_id is not None:
        conditions.append(TaskResults.user_id == user_id)
    
    # Р—Р°РїСЂРѕСЃ СЃ join Рє tasks РґР»СЏ С„РёР»СЊС‚СЂР°С†РёРё РїРѕ course_id
    query = (
        select(TaskResults)
        .join(Tasks, TaskResults.task_id == Tasks.id)
        .where(and_(*conditions))
        .order_by(TaskResults.submitted_at.desc())
        .limit(limit)
        .offset(offset)
    )
    
    result = await db.execute(query)
    results = result.scalars().all()
    
    return [TaskResultRead.model_validate(r) for r in results]
