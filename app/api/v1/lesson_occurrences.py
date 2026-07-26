"""
Student-эндпоинты явки и переноса (tsk-429/tsk-430, Календарь LMS Фаза 2-3).

- `POST /lesson-occurrences/{id}/attendance` — подтвердить/отказаться.
- `GET  /me/lesson-occurrences` — список занятий текущего ученика.
- `GET  /lesson-occurrences/available-slots?occurrence_id=` — кандидаты для
  переноса (Фаза 3).
- `POST /lesson-occurrences/{id}/reschedule` — перенести занятие (Фаза 3).
- `POST /lesson-occurrences/ad-hoc` — отработка вне расписания (Фаза 3).

Гейт: `require_authenticated` (реальный пользователь, не сервисный токен) —
ownership (occurrence.student_id == current_user.id) проверяется в сервисе,
роутер только резолвит CurrentUser (паттерн `me_notifications.py`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_authenticated
from app.auth.current_user import CurrentUser
from app.schemas.lesson_calendar import (
    AdHocRequest,
    AttendanceActionRequest,
    AvailableSlotOption,
    LessonOccurrenceRead,
    RescheduleRequest,
)
from app.services import lesson_attendance_service, lesson_occurrence_service

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


@router.get("/lesson-occurrences/available-slots", response_model=list[AvailableSlotOption])
async def get_available_slots(
    occurrence_id: int = Query(..., description="Занятие, которое переносится"),
    limit: int = Query(default=10, ge=1, le=30),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(require_authenticated),
) -> list[AvailableSlotOption]:
    candidates = await lesson_occurrence_service.list_available_slots(
        db, occurrence_id=occurrence_id, student_id=current_user.id, limit=limit,
    )
    return [AvailableSlotOption(scheduled_at=dt) for dt in candidates]


@router.post(
    "/lesson-occurrences/{occurrence_id}/reschedule",
    response_model=LessonOccurrenceRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        403: {"description": "Занятие принадлежит другому ученику"},
        404: {"description": "Занятие не найдено"},
        409: {"description": "Занятие уже закрыто или новое время занято"},
        422: {"description": "Новое время вне часов работы школы"},
    },
)
async def post_reschedule(
    occurrence_id: int,
    body: RescheduleRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(require_authenticated),
) -> LessonOccurrenceRead:
    new_occurrence = await lesson_occurrence_service.reschedule_occurrence(
        db,
        occurrence_id=occurrence_id,
        student_id=current_user.id,
        new_scheduled_at=body.new_scheduled_at,
    )
    return LessonOccurrenceRead.model_validate(new_occurrence)


@router.post(
    "/lesson-occurrences/ad-hoc",
    response_model=LessonOccurrenceRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_ad_hoc(
    body: AdHocRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(require_authenticated),
) -> LessonOccurrenceRead:
    occurrence = await lesson_occurrence_service.create_ad_hoc_occurrence(
        db,
        student_id=current_user.id,
        teacher_id=body.teacher_id,
        scheduled_at=body.scheduled_at,
        duration_minutes=body.duration_minutes,
    )
    return LessonOccurrenceRead.model_validate(occurrence)
