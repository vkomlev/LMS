"""
Learning API V1 (этап 3): next-item, materials/complete, tasks/start-or-get-attempt, state, request-help.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Path, Body, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models.attempts import Attempts
from app.models.tasks import Tasks
from app.schemas.learning_api import (
    NextItemResponse,
    MaterialCompleteRequest,
    MaterialCompleteResponse,
    StartOrGetAttemptRequest,
    StartOrGetAttemptResponse,
    TaskStateResponse,
    RequestHelpRequest,
    RequestHelpResponse,
)
from app.services.learning_engine_service import LearningEngineService
from app.services.learning_events_service import record_help_requested, set_material_completed
from app.services.attempts_service import AttemptsService
from app.services.tasks_service import TasksService
from app.services.materials_service import MaterialsService
from app.services.users_service import UsersService

router = APIRouter(prefix="/learning", tags=["learning"])
logger = logging.getLogger("api.learning")

learning_service = LearningEngineService()
attempts_service = AttemptsService()
tasks_service = TasksService()
materials_service = MaterialsService()
users_service = UsersService()


# ----- GET /learning/next-item -----

@router.get(
    "/next-item",
    response_model=NextItemResponse,
    summary="Следующий шаг для студента (material | task | none | blocked_*)",
)
async def get_next_item(
    student_id: int = Query(..., description="ID студента"),
    db: AsyncSession = Depends(get_db),
) -> NextItemResponse:
    user = await users_service.get_by_id(db, student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    result = await learning_service.resolve_next_item(db, student_id)
    if result.type in ("blocked_dependency", "blocked_limit"):
        logger.warning(
            "next-item: student_id=%s type=%s course_id=%s",
            student_id, result.type, result.course_id,
        )
    else:
        logger.info(
            "next-item: student_id=%s type=%s course_id=%s material_id=%s task_id=%s",
            student_id, result.type, result.course_id, result.material_id, result.task_id,
        )
    await db.commit()
    return NextItemResponse(
        type=result.type,
        course_id=result.course_id,
        material_id=result.material_id,
        task_id=result.task_id,
        reason=result.reason,
        dependency_course_id=result.dependency_course_id,
    )


# ----- POST /learning/materials/{material_id}/complete -----

@router.post(
    "/materials/{material_id}/complete",
    response_model=MaterialCompleteResponse,
    summary="Отметить материал как пройденный (идемпотентно)",
)
async def material_complete(
    material_id: int = Path(..., description="ID материала"),
    body: MaterialCompleteRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> MaterialCompleteResponse:
    material = await materials_service.get_by_id(db, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Материал не найден")
    user = await users_service.get_by_id(db, body.student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    completed_at = await set_material_completed(db, body.student_id, material_id)
    await db.commit()
    logger.info("material complete: student_id=%s material_id=%s", body.student_id, material_id)
    return MaterialCompleteResponse(
        ok=True,
        student_id=body.student_id,
        material_id=material_id,
        status="completed",
        completed_at=completed_at,
    )


# ----- POST /learning/tasks/{task_id}/start-or-get-attempt -----

@router.post(
    "/tasks/{task_id}/start-or-get-attempt",
    response_model=StartOrGetAttemptResponse,
    summary="Начать попытку или вернуть текущую незавершённую (идемпотентно)",
)
async def start_or_get_attempt(
    task_id: int = Path(..., description="ID задания"),
    body: StartOrGetAttemptRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> StartOrGetAttemptResponse:
    task = await tasks_service.get_by_id(db, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание не найдено")
    user = await users_service.get_by_id(db, body.student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    course_id = task.course_id

    # Активная попытка по этому курсу (finished_at IS NULL)
    stmt = (
        select(Attempts)
        .where(
            Attempts.user_id == body.student_id,
            Attempts.course_id == course_id,
            Attempts.finished_at.is_(None),
        )
        .order_by(Attempts.created_at.desc())
        .limit(1)
    )
    r = await db.execute(stmt)
    existing = r.scalar_one_or_none()
    if existing is not None:
        await db.commit()
        return StartOrGetAttemptResponse(
            attempt_id=existing.id,
            user_id=existing.user_id,
            course_id=existing.course_id,
            created_at=existing.created_at,
            finished_at=existing.finished_at,
            source_system=existing.source_system,
        )

    attempt = await attempts_service.create_attempt(
        db=db,
        user_id=body.student_id,
        course_id=course_id,
        source_system=body.source_system or "learning_api",
        meta=None,
    )
    await db.commit()
    logger.info(
        "start-or-get-attempt: student_id=%s task_id=%s attempt_id=%s",
        body.student_id, task_id, attempt.id,
    )
    return StartOrGetAttemptResponse(
        attempt_id=attempt.id,
        user_id=attempt.user_id,
        course_id=attempt.course_id,
        created_at=attempt.created_at,
        finished_at=attempt.finished_at,
        source_system=attempt.source_system,
    )


# ----- GET /learning/tasks/{task_id}/state -----

@router.get(
    "/tasks/{task_id}/state",
    response_model=TaskStateResponse,
    summary="Состояние задания по последней завершённой попытке",
)
async def get_task_state(
    task_id: int = Path(..., description="ID задания"),
    student_id: int = Query(..., description="ID студента"),
    db: AsyncSession = Depends(get_db),
) -> TaskStateResponse:
    task = await tasks_service.get_by_id(db, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание не найдено")
    user = await users_service.get_by_id(db, student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    state = await learning_service.compute_task_state(db, student_id, task_id)
    return TaskStateResponse(
        task_id=task_id,
        student_id=student_id,
        state=state.state,
        last_attempt_id=state.last_attempt_id,
        last_score=state.last_score,
        last_max_score=state.last_max_score,
        last_finished_at=state.last_finished_at,
        attempts_used=state.attempts_used,
        attempts_limit_effective=state.attempts_limit_effective,
    )


# ----- POST /learning/tasks/{task_id}/request-help -----

@router.post(
    "/tasks/{task_id}/request-help",
    response_model=RequestHelpResponse,
    summary="Запросить помощь по заданию (идемпотентно в окне дедупа)",
)
async def request_help(
    task_id: int = Path(..., description="ID задания"),
    body: RequestHelpRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> RequestHelpResponse:
    task = await tasks_service.get_by_id(db, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание не найдено")
    user = await users_service.get_by_id(db, body.student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    event_id, deduplicated = await record_help_requested(
        db, body.student_id, task_id, body.message
    )
    await db.commit()
    logger.info(
        "request-help: student_id=%s task_id=%s event_id=%s deduplicated=%s",
        body.student_id, task_id, event_id, deduplicated,
    )
    return RequestHelpResponse(ok=True, event_id=event_id, deduplicated=deduplicated)
