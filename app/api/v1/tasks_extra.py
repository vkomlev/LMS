from __future__ import annotations

from fastapi import APIRouter, Depends, Body
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, List, Literal
from pydantic import BaseModel

from app.api.deps import get_db
from app.schemas.tasks import (
    TaskRead, 
    TaskBulkUpsertRequest, 
    TaskBulkUpsertResponse, 
    TaskBulkUpsertResultItem, 
    TaskValidateResponse, 
    TaskValidateRequest,
    TaskFindByExternalResponse,
    TaskFindByExternalItem,
    TaskFindByExternalRequest,
)
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
    "/tasks/validate",
    response_model=TaskValidateResponse,
    summary="Массовая предварительная валидация задания перед импортом",
)
async def validate_task_endpoint(
    payload: TaskValidateRequest = Body(
        ...,
        description="Данные задания для предварительной валидации",
    ),
    db: AsyncSession = Depends(get_db),
) -> TaskValidateResponse:
    """
    Предварительная проверка структуры и ссылочных данных задания до записи в БД.

    Проверяем:
    - структуру task_content (например, наличие options[].id),
    - ключевые поля solution_rules (например, max_score),
    - существование difficulty по difficulty_code,
    - существование course по course_code.

    Запись в БД не выполняется, возвращается только флаг is_valid и список ошибок.
    """
    is_valid, errors = await tasks_service.validate_task_import(
        db,
        task_content=payload.task_content,
        solution_rules=payload.solution_rules,
        difficulty_code=payload.difficulty_code,
        difficulty_id=payload.difficulty_id,
        course_code=payload.course_code,
        external_uid=payload.external_uid,
    )
    return TaskValidateResponse(is_valid=is_valid, errors=errors)


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

@router.post(
    "/tasks/find-by-external",
    response_model=TaskFindByExternalResponse,
    summary="Массовое получение задач по списку external_uid",
)
async def find_tasks_by_external_uid_endpoint(
    payload: TaskFindByExternalRequest,
    db: AsyncSession = Depends(get_db),
) -> TaskFindByExternalResponse:
    """
    Массовое получение задач по external_uid.

    Возвращает только существующие задачи.
    Если часть UID отсутствует — они просто не попадут в список.
    """
    results = await tasks_service.find_by_external_uids(db, uids=payload.uids)

    items = [
        TaskFindByExternalItem(external_uid=uid, id=id_)
        for uid, id_ in results
    ]

    return TaskFindByExternalResponse(items=items)