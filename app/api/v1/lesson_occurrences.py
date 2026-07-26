"""
Student-эндпоинты явки и переноса (tsk-429/430/435, Календарь LMS).

- `POST /lesson-occurrences/{id}/attendance` — подтвердить/отказаться (по
  своему участию — occurrence может быть групповым).
- `GET  /me/lesson-occurrences` — список занятий текущего ученика (свой
  статус участия, без списка остальных участников группы — приватность).
- `GET  /lesson-occurrences/available-slots?occurrence_id=` — кандидаты для
  переноса СВОЕГО участия.
- `POST /lesson-occurrences/{id}/reschedule` — перенести своё участие (не
  трогает остальных участников группового occurrence).
- `POST /lesson-occurrences/ad-hoc` — отработка вне расписания.

Гейт: `require_authenticated` (реальный пользователь, не сервисный токен) —
ownership (наличие своей строки участника) проверяется в сервисе, роутер
только резолвит CurrentUser (паттерн `me_notifications.py`).
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
    MyLessonOccurrenceRead,
    RescheduleRequest,
)
from app.services import lesson_attendance_service, lesson_occurrence_service

router = APIRouter(tags=["lesson_occurrences"])


def _to_my_occurrence_read(participant, occurrence) -> MyLessonOccurrenceRead:
    data = LessonOccurrenceRead.model_validate(occurrence).model_dump()
    data["participant_id"] = participant.id
    data["my_status"] = participant.status
    return MyLessonOccurrenceRead(**data)


@router.post(
    "/lesson-occurrences/{occurrence_id}/attendance",
    response_model=MyLessonOccurrenceRead,
    responses={
        403: {"description": "Ученик не входит в число участников этого занятия"},
        404: {"description": "Занятие не найдено"},
        409: {"description": "Участие уже в закрытом статусе (no_show/completed/rescheduled)"},
    },
)
async def post_attendance(
    occurrence_id: int,
    request: Request,
    body: AttendanceActionRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(require_authenticated),
) -> MyLessonOccurrenceRead:
    ip = request.client.host if request.client else None
    participant, occurrence = await lesson_attendance_service.record_attendance(
        db,
        occurrence_id=occurrence_id,
        student_id=current_user.id,
        action=body.action,
        ip=ip,
    )
    return _to_my_occurrence_read(participant, occurrence)


@router.get("/me/lesson-occurrences", response_model=list[MyLessonOccurrenceRead])
async def list_my_occurrences(
    from_dt: Optional[datetime] = Query(default=None, alias="from"),
    to_dt: Optional[datetime] = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(require_authenticated),
) -> list[MyLessonOccurrenceRead]:
    pairs = await lesson_attendance_service.list_student_occurrences(
        db,
        student_id=current_user.id,
        from_dt=from_dt,
        to_dt=to_dt,
        limit=limit,
    )
    return [_to_my_occurrence_read(p, o) for p, o in pairs]


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
    response_model=MyLessonOccurrenceRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        403: {"description": "Ученик не входит в число участников этого занятия"},
        404: {"description": "Занятие не найдено"},
        409: {"description": "Участие уже закрыто или новое время занято"},
        422: {"description": "Новое время вне часов работы школы"},
    },
)
async def post_reschedule(
    occurrence_id: int,
    body: RescheduleRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(require_authenticated),
) -> MyLessonOccurrenceRead:
    new_occurrence, new_participant = await lesson_occurrence_service.reschedule_occurrence(
        db,
        occurrence_id=occurrence_id,
        student_id=current_user.id,
        new_scheduled_at=body.new_scheduled_at,
    )
    return _to_my_occurrence_read(new_participant, new_occurrence)


@router.post(
    "/lesson-occurrences/ad-hoc",
    response_model=MyLessonOccurrenceRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_ad_hoc(
    body: AdHocRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(require_authenticated),
) -> MyLessonOccurrenceRead:
    occurrence, participant = await lesson_occurrence_service.create_ad_hoc_occurrence(
        db,
        student_id=current_user.id,
        teacher_id=body.teacher_id,
        scheduled_at=body.scheduled_at,
        duration_minutes=body.duration_minutes,
    )
    return _to_my_occurrence_read(participant, occurrence)
