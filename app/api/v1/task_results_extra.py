from __future__ import annotations

from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Body, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.api.deps import get_db
from app.schemas.task_results import TaskResultRead, TaskResultUpdate
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


@router.post(
    "/task-results/{result_id}/manual-check",
    response_model=TaskResultRead,
    summary="Ручная дооценка результата",
    responses={
        200: {
            "description": "Результат успешно обновлен",
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
            "description": "Неверные параметры запроса",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "score не может быть больше max_score"
                    }
                }
            }
        },
        404: {
            "description": "Результат не найден",
        },
    },
)
async def manual_check_task_result(
    result_id: int,
    payload: dict = Body(
        ...,
        description="Параметры ручной проверки",
        examples=[
            {
                "summary": "Ручная проверка с новым баллом",
                "value": {
                    "score": 15,
                    "checked_by": 2,
                }
            },
            {
                "summary": "Ручная проверка с обновлением is_correct",
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
    Выполнить ручную дооценку результата выполнения задачи.
    
    Позволяет преподавателю или администратору изменить оценку,
    установленную автоматической проверкой, или проверить задачу,
    требующую ручной проверки.
    
    Args:
        result_id: ID результата для проверки.
        payload: Параметры проверки:
            - score: Новый балл (обязательно)
            - checked_by: ID пользователя, выполняющего проверку (обязательно)
            - is_correct: Флаг правильности ответа (опционально)
            - metrics: Дополнительные метрики (опционально)
    
    Returns:
        Обновленный результат.
    """
    from app.schemas.task_results import TaskResultUpdate
    
    score = payload.get("score")
    checked_by = payload.get("checked_by")
    
    if score is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Параметр 'score' обязателен",
        )
    
    if checked_by is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Параметр 'checked_by' обязателен",
        )
    
    # Получаем результат
    result = await task_results_service.get_by_id(db, result_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Результат с ID {result_id} не найден",
        )
    
    # Проверяем, что score не превышает max_score
    max_score = result.max_score or 0
    if max_score > 0 and score > max_score:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"score ({score}) не может быть больше max_score ({max_score})",
        )
    
    if score < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="score не может быть отрицательным",
        )
    
    # Обновляем результат
    update_data = TaskResultUpdate(
        score=score,
        checked_by=checked_by,
        checked_at=datetime.now(),
        is_correct=payload.get("is_correct"),
        metrics=payload.get("metrics"),
    )
    
    # BaseService.update ожидает объект БД и dict, поэтому сначала получаем объект
    updated_result = await task_results_service.update(
        db, 
        result, 
        update_data.model_dump(exclude_unset=True)
    )
    return TaskResultRead.model_validate(updated_result)


@router.get(
    "/task-results/stats/by-task/{task_id}",
    summary="Статистика по задаче",
    responses={
        200: {
            "description": "Статистика по задаче",
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
    Получить статистику по задаче.
    
    Возвращает:
    - total_attempts: Общее количество попыток
    - average_score: Средний балл
    - correct_percentage: Процент правильных ответов
    - min_score, max_score: Минимальный и максимальный баллы
    - score_distribution: Распределение баллов
    """
    return await task_results_service.get_stats_by_task(db, task_id)


@router.get(
    "/task-results/stats/by-course/{course_id}",
    summary="Статистика по курсу",
    responses={
        200: {
            "description": "Статистика по курсу",
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
    Получить статистику по курсу.
    
    Возвращает агрегированную статистику по всем задачам курса:
    - total_attempts: Общее количество попыток
    - average_score: Средний балл
    - correct_percentage: Процент правильных ответов
    - tasks_count: Количество задач в курсе
    """
    return await task_results_service.get_stats_by_course(db, course_id)


@router.get(
    "/task-results/stats/by-user/{user_id}",
    summary="Статистика по пользователю",
    responses={
        200: {
            "description": "Статистика по пользователю",
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
    Получить статистику по пользователю.
    
    Возвращает:
    - total_attempts: Общее количество попыток
    - average_score: Средний балл
    - correct_percentage: Процент правильных ответов
    - total_score: Сумма всех баллов
    - total_max_score: Сумма максимальных баллов
    - completion_percentage: Процент выполнения (total_score / total_max_score * 100)
    """
    return await task_results_service.get_stats_by_user(db, user_id)


@router.get(
    "/task-results/by-pending-review",
    response_model=List[TaskResultRead],
    summary="Получить результаты заданий, требующих ручной проверки",
    responses={
        200: {
            "description": "Список результатов, требующих проверки",
        },
    },
)
async def get_pending_review_results(
    course_id: Optional[int] = Query(None, description="Фильтр по курсу"),
    user_id: Optional[int] = Query(None, description="Фильтр по пользователю"),
    limit: int = Query(50, ge=1, le=1000, description="Максимум записей на странице"),
    offset: int = Query(0, ge=0, description="Смещение"),
    db: AsyncSession = Depends(get_db),
) -> List[TaskResultRead]:
    """
    Получить список результатов заданий, требующих ручной проверки.
    
    Возвращает результаты, где:
    - checked_at = null (еще не проверены)
    
    Args:
        course_id: Опциональный фильтр по курсу
        user_id: Опциональный фильтр по пользователю
        limit: Максимум записей на странице (1-1000)
        offset: Смещение для пагинации
    
    Returns:
        Список результатов, требующих проверки
    """
    from sqlalchemy import select, and_, or_
    from app.models.task_results import TaskResults
    from app.models.tasks import Tasks
    
    # Базовое условие: результаты не проверены
    conditions = [TaskResults.checked_at.is_(None)]
    
    # Дополнительные фильтры
    if course_id is not None:
        # Нужно присоединить tasks для фильтрации по course_id
        conditions.append(Tasks.course_id == course_id)
    
    if user_id is not None:
        conditions.append(TaskResults.user_id == user_id)
    
    # Запрос с join к tasks для фильтрации по course_id
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
