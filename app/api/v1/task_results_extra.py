from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.task_results import TaskResultRead
from app.services.task_results_service import TaskResultsService


router = APIRouter(tags=["task_results"])

task_results_service = TaskResultsService()


@router.get(
    "/task-results/by-user/{user_id}",
    response_model=List[TaskResultRead],
    summary="Получить результаты пользователя",
    responses={
        200: {
            "description": "Список результатов пользователя",
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
    limit: int = Query(100, ge=1, le=1000, description="Максимум записей на странице"),
    offset: int = Query(0, ge=0, description="Смещение"),
) -> List[TaskResultRead]:
    """
    Получить список результатов выполнения заданий пользователя с пагинацией.

    Args:
        user_id: ID пользователя.
        limit: Максимум записей на странице (1-1000).
        offset: Смещение для пагинации.

    Returns:
        Список результатов пользователя.
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
    summary="Получить результаты по задаче",
    responses={
        200: {
            "description": "Список результатов по задаче",
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
            "description": "Задача не найдена",
        },
    },
)
async def get_task_results_by_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000, description="Максимум записей на странице"),
    offset: int = Query(0, ge=0, description="Смещение"),
) -> List[TaskResultRead]:
    """
    Получить список результатов выполнения конкретной задачи с пагинацией.

    Args:
        task_id: ID задачи.
        limit: Максимум записей на странице (1-1000).
        offset: Смещение для пагинации.

    Returns:
        Список результатов по задаче.
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
    summary="Получить результаты попытки",
    responses={
        200: {
            "description": "Список результатов попытки",
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
            "description": "Попытка не найдена",
        },
    },
)
async def get_task_results_by_attempt(
    attempt_id: int,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000, description="Максимум записей на странице"),
    offset: int = Query(0, ge=0, description="Смещение"),
) -> List[TaskResultRead]:
    """
    Получить список результатов выполнения заданий в рамках конкретной попытки с пагинацией.

    Args:
        attempt_id: ID попытки.
        limit: Максимум записей на странице (1-1000).
        offset: Смещение для пагинации.

    Returns:
        Список результатов попытки.
    """
    results, total = await task_results_service.get_by_attempt(
        db,
        attempt_id=attempt_id,
        limit=limit,
        offset=offset,
    )
    return [TaskResultRead.model_validate(result) for result in results]
