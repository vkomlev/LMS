"""
Student-эндпоинты явки (tsk-429, Календарь LMS Фаза 2).

- `POST /lesson-occurrences/{id}/attendance` — подтвердить/отказаться.
- `GET  /me/lesson-occurrences` — список занятий текущего ученика.

Гейт: `require_authenticated` (реальный пользователь, не сервисный токен) —
ownership (occurrence.student_id == current_user.id) проверяется в сервисе,
роутер только резолвит CurrentUser (паттерн `me_notifications.py`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_authenticated
from app.auth.current_user import CurrentUser
from app.schemas.lesson_calendar import (
    AttendanceActionRequest,
    LessonOccurrenceRead,
)
from app.services import lesson_attendance_service

router = APIRouter(tags=["lesson_occurrences"])


@router.post(
    "/lesson-occurrences/{occurrence_id}/attendance",
    response_model=LessonOccurrenceRead,
    responses={
        403: {"description": "Занятие принадлежит другому ученику"},
        404: {"description": "Занятие не найдено"},
        409: {"description": "Занятие уже в закрытом статусе (no_show/completed/rescheduled)"},
    },
)
async def post_attendance(
    occurrence_id: int,
    request: Request,
    body: AttendanceActionRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(require_authenticated),
) -> LessonOccurrenceRead:
    ip = request.client.host if request.client else None
    occurrence = await lesson_attendance_service.record_attendance(
        db,
        occurrence_id=occurrence_id,
        student_id=current_user.id,
        action=body.action,
        ip=ip,
    )
    return LessonOccurrenceRead.model_validate(occurrence)


@router.get("/me/lesson-occurrences", response_model=list[LessonOccurrenceRead])
async def list_my_occurrences(
    from_dt: Optional[datetime] = Query(default=None, alias="from"),
    to_dt: Optional[datetime] = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(require_authenticated),
) -> list[LessonOccurrenceRead]:
    rows = await lesson_attendance_service.list_student_occurrences(
        db,
        student_id=current_user.id,
        from_dt=from_dt,
        to_dt=to_dt,
        limit=limit,
    )
    return [LessonOccurrenceRead.model_validate(r) for r in rows]
