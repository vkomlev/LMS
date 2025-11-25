from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.tasks import TaskRead
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
