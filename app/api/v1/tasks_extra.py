from __future__ import annotations

from fastapi import APIRouter, Depends, Body
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, List, Literal
from pydantic import BaseModel

from app.api.deps import get_db
from app.schemas.tasks import TaskRead, TaskBulkUpsertRequest, TaskBulkUpsertResponse, TaskBulkUpsertResultItem
from app.services.tasks_service import TasksService


router = APIRouter(tags=["tasks"])

tasks_service = TasksService()


@router.get(
    "/tasks/by-external/{external_uid}",
    response_model=TaskRead,
    summary="Получить задачу по внешнему идентификатору",
)
async def get_task_by_external_uid(
    external_uid: str,
    db: AsyncSession = Depends(get_db),
) -> TaskRead:
    """
    Вернуть задачу по внешнему устойчивому идентификатору.

    Статусы:
    - 200 — если задача найдена;
    - 404 — если задача не найдена (генерируется DomainError в сервисе).
    """
    task = await tasks_service.get_by_external_uid(db, external_uid=external_uid)
    return task

@router.post(
    "/tasks/bulk-upsert",
    response_model=TaskBulkUpsertResponse,
    summary="Массовый upsert задач по external_uid",
)
async def bulk_upsert_tasks_endpoint(
    payload: TaskBulkUpsertRequest = Body(
        ...,
        description="Список задач для массового upsert'а",
    ),
    db: AsyncSession = Depends(get_db),
) -> TaskBulkUpsertResponse:
    """
    Массовый upsert задач.

    Правила:
    - если external_uid не найден → создаём задачу (action = 'created');
    - если найден → обновляем существующую задачу (action = 'updated').

    Это позволяет существенно ускорить импорт из Google Sheets:
    одно HTTP-обращение вместо сотен.
    """
    raw_results = await tasks_service.bulk_upsert(
        db,
        items=[item.model_dump() for item in payload.items],
    )

    results = [
        TaskBulkUpsertResultItem(
            external_uid=external_uid,
            action=action,  # "created" | "updated"
            id=task_id,
        )
        for external_uid, action, task_id in raw_results
    ]

    return TaskBulkUpsertResponse(results=results)
