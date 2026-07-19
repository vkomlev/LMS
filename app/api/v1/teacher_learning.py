"""
Learning API V1 (этап 3): teacher endpoint — переопределение лимита попыток по заданию.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, Body, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, get_current_user
from app.auth.current_user import CurrentUser
from app.schemas.learning_api import TaskLimitOverrideRequest, TaskLimitOverrideResponse
from app.services.learning_events_service import record_task_limit_override
from app.services.teacher_queue_service import teacher_can_override_limit
from app.services.users_service import UsersService
from app.services.tasks_service import TasksService

router = APIRouter(prefix="/teacher", tags=["teacher_learning"])
logger = logging.getLogger("api.teacher_learning")

users_service = UsersService()
tasks_service = TasksService()


@router.post(
    "/task-limits/override",
    response_model=TaskLimitOverrideResponse,
    summary="Установить/обновить лимит попыток по заданию для студента (идемпотентно)",
)
async def task_limit_override(
    body: TaskLimitOverrideRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> TaskLimitOverrideResponse:
    # tsk-298 Фаза 3-Ⅱ: открыт cookie-преподавателю (был сервис-only, без ACL —
    # закрывает старый TODO). Identity: updated_by = сам преподаватель; ACL:
    # авторизован на задачу этого ученика (course-tree / свой ученик / methodist).
    # Сервисный токен (TG-бот) — bypass.
    if not current_user.is_service:
        if current_user.id != body.updated_by:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
        if not await teacher_can_override_limit(
            db, body.updated_by, body.student_id, body.task_id
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Работа вне вашей зоны ответственности")

    student = await users_service.get_by_id(db, body.student_id)
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    task = await tasks_service.get_by_id(db, body.task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание не найдено")
    updated_by_user = await users_service.get_by_id(db, body.updated_by)
    if updated_by_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь updated_by не найден")

    await db.execute(
        text("""
            INSERT INTO student_task_limit_override
            (student_id, task_id, max_attempts_override, reason, updated_by, updated_at)
            VALUES (:student_id, :task_id, :max_attempts_override, :reason, :updated_by, now())
            ON CONFLICT (student_id, task_id)
            DO UPDATE SET
                max_attempts_override = EXCLUDED.max_attempts_override,
                reason = EXCLUDED.reason,
                updated_by = EXCLUDED.updated_by,
                updated_at = now()
        """),
        {
            "student_id": body.student_id,
            "task_id": body.task_id,
            "max_attempts_override": body.max_attempts_override,
            "reason": body.reason,
            "updated_by": body.updated_by,
        },
    )
    await record_task_limit_override(
        db,
        body.student_id,
        body.task_id,
        body.max_attempts_override,
        body.reason,
        body.updated_by,
    )

    r = await db.execute(
        text("""
            SELECT updated_at FROM student_task_limit_override
            WHERE student_id = :student_id AND task_id = :task_id
        """),
        {"student_id": body.student_id, "task_id": body.task_id},
    )
    row = r.fetchone()
    updated_at = row[0] if row else None
    await db.commit()
    logger.info(
        "task-limits/override: student_id=%s task_id=%s max=%s updated_by=%s",
        body.student_id, body.task_id, body.max_attempts_override, body.updated_by,
    )
    if updated_at is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ошибка чтения updated_at")
    return TaskLimitOverrideResponse(
        ok=True,
        student_id=body.student_id,
        task_id=body.task_id,
        max_attempts_override=body.max_attempts_override,
        updated_at=updated_at,
    )
