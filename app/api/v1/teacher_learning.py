"""
Learning API V1 (этап 3): teacher endpoint — переопределение лимита попыток по заданию.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, Body, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from typing import Optional

from app.api.deps import get_bare_db, get_current_user
from app.auth.current_user import CurrentUser
from app.schemas.learning_api import TaskLimitOverrideRequest, TaskLimitOverrideResponse
from app.schemas.task_content import QUIZ_TASK_TYPES
from app.services import audit_service
from app.services.learning_engine_service import DEFAULT_MAX_ATTEMPTS
from app.services.learning_events_service import record_task_limit_override
from app.services.teacher_queue_service import teacher_can_override_limit
from app.services.users_service import UsersService
from app.services.tasks_service import TasksService

router = APIRouter(prefix="/teacher", tags=["teacher_learning"])
logger = logging.getLogger("api.teacher_learning")

users_service = UsersService()
tasks_service = TasksService()

# tsk-335: короткое окно защиты от накрутки повторным кликом. Нужно ТОЛЬКО
# режиму grant_same_again — он аддитивный (каждый успешный вызов поднимает
# лимит ещё раз), в отличие от explicit-upsert (идемпотентен сам по себе,
# абсолютное значение). Основная защита — дизейбл кнопки на клиенте
# (isMutating); это бэкенд-страховка на гонку сети/двойной тап.
_GRANT_SAME_AGAIN_DEBOUNCE_SEC = 3


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

    # tsk-335: квиз-вопрос (SC_Qw/MC_Qw) всегда ограничен одной попыткой —
    # get_effective_attempt_limit перебивает любой override нулём смысла.
    # Раньше explicit-режим тихо принимал override для квиза (записывался,
    # но не действовал) — закрываем заодно как побочный, но прямо
    # мотивированный этим же путём фикс.
    task_type = (task.task_content or {}).get("type")
    if task_type in QUIZ_TASK_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Квиз-вопрос всегда ограничен одной попыткой — лимит увеличить нельзя",
        )

    # Сериализация гонки по паре (student, task) — тот же паттерн, что
    # get_or_create_blocked_limit_help_request (help_requests_service.py) и
    # manual_progress_service._lock.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
        {"k1": body.student_id, "k2": body.task_id},
    )

    row = (
        await db.execute(
            text(
                "SELECT max_attempts_override, updated_by, "
                "       (now() - updated_at) <= make_interval(secs => :debounce_sec) AS is_recent "
                "FROM student_task_limit_override "
                "WHERE student_id = :student_id AND task_id = :task_id"
            ),
            {
                "student_id": body.student_id,
                "task_id": body.task_id,
                "debounce_sec": _GRANT_SAME_AGAIN_DEBOUNCE_SEC,
            },
        )
    ).fetchone()
    previous: Optional[int] = int(row[0]) if row else None

    deduped = (
        body.mode == "grant_same_again"
        and row is not None
        and row[1] == body.updated_by
        and bool(row[2])
    )

    base_added: Optional[int] = None
    if deduped:
        new_limit = previous
    elif body.mode == "grant_same_again":
        base = task.max_attempts if task.max_attempts is not None else DEFAULT_MAX_ATTEMPTS
        # "Текущий эффективный" здесь — previous (если override уже стоял) либо
        # base; get_effective_attempt_limit() читал бы ту же таблицу повторно —
        # previous уже прочитан выше, лишний запрос не нужен.
        current_effective = previous if previous is not None else base
        new_limit = current_effective + base
        base_added = base
    else:
        new_limit = body.max_attempts_override

    if not deduped:
        reason = body.reason
        if reason is None and body.mode == "grant_same_again":
            reason = (
                f"Выдано ещё попыток: +{base_added} "
                f"(эффективный лимит {previous or 0} → {new_limit})"
            )
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
                "max_attempts_override": new_limit,
                "reason": reason,
                "updated_by": body.updated_by,
            },
        )
        await record_task_limit_override(
            db,
            body.student_id,
            body.task_id,
            new_limit,
            reason,
            body.updated_by,
            mode=body.mode,
            previous_max_attempts_override=previous,
            base_attempts_added=base_added,
        )
        await audit_service.log_event(
            db,
            audit_service.TEACHER_LIMIT_OVERRIDE,
            user_id=body.updated_by,
            details={
                "student_id": body.student_id,
                "task_id": body.task_id,
                "mode": body.mode,
                "previous_max_attempts_override": previous,
                "max_attempts_override": new_limit,
                "base_attempts_added": base_added,
                "reason": reason,
            },
        )

    r = await db.execute(
        text("""
            SELECT updated_at FROM student_task_limit_override
            WHERE student_id = :student_id AND task_id = :task_id
        """),
        {"student_id": body.student_id, "task_id": body.task_id},
    )
    row2 = r.fetchone()
    updated_at = row2[0] if row2 else None
    await db.commit()
    logger.info(
        "task-limits/override: student_id=%s task_id=%s mode=%s max=%s "
        "base_added=%s already=%s updated_by=%s",
        body.student_id, body.task_id, body.mode, new_limit,
        base_added, deduped, body.updated_by,
    )
    if updated_at is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ошибка чтения updated_at")
    return TaskLimitOverrideResponse(
        ok=True,
        student_id=body.student_id,
        task_id=body.task_id,
        max_attempts_override=new_limit,
        previous_max_attempts_override=previous,
        mode=body.mode,
        base_attempts_added=base_added,
        already=deduped,
        updated_at=updated_at,
    )
