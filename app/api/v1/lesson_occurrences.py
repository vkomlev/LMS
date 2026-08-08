"""
Student-эндпоинты явки и переноса (tsk-429/430/435, Календарь LMS).

- `POST /lesson-occurrences/{id}/attendance` — подтвердить/отказаться (по
  своему участию — occurrence может быть групповым).
- `GET  /me/lesson-occurrences` — список занятий текущего ученика (свой
  статус участия, без списка остальных участников группы — приватность).
- `GET  /lesson-occurrences/available-slots?occurrence_id=` — кандидаты для
  переноса СВОЕГО участия (времена реальных слотов расписания, tsk-587).
- `POST /lesson-occurrences/{id}/reschedule` — перенести своё участие (не
  трогает остальных участников группового occurrence).
- `POST /lesson-occurrences/ad-hoc` — запись на отработку; время обязано
  совпадать с началом слота расписания (tsk-587).

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
    BookableOccurrenceRead,
    LessonOccurrenceRead,
    MyLessonOccurrenceRead,
    RescheduleRequest,
)
from app.services import lesson_attendance_service, lesson_occurrence_service
from app.services.student_teacher_links_service import StudentTeacherLinksService

_student_teacher_links_service = StudentTeacherLinksService()

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


@router.get("/me/lesson-occurrences/bookable", response_model=list[BookableOccurrenceRead])
async def list_bookable_occurrences(
    limit: int = Query(default=10, ge=1, le=30),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(require_authenticated),
) -> list[BookableOccurrenceRead]:
    """Ближайшие уже существующие занятия преподавателей ученика, куда можно
    присоединиться (tsk-021/443) — вместо свободного ввода даты/времени и
    создания отдельного ad-hoc occurrence на то же время."""
    teachers = await _student_teacher_links_service.list_teachers(db, current_user.id)
    pairs = await lesson_occurrence_service.list_bookable_occurrences_for_student(
        db, student_id=current_user.id, teacher_ids=[t.id for t in teachers], limit=limit,
    )
    return [
        BookableOccurrenceRead(
            id=o.id, scheduled_at=o.scheduled_at, duration_minutes=o.duration_minutes,
            teacher_names=names,
        )
        for o, names in pairs
    ]


@router.post(
    "/lesson-occurrences/{occurrence_id}/join",
    response_model=MyLessonOccurrenceRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Занятие не найдено"},
        409: {"description": "Занятие уже прошло или пересекается с другим активным занятием"},
    },
)
async def post_join(
    occurrence_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(require_authenticated),
) -> MyLessonOccurrenceRead:
    occurrence, participant = await lesson_occurrence_service.join_occurrence_as_student(
        db, occurrence_id=occurrence_id, student_id=current_user.id,
    )
    return _to_my_occurrence_read(participant, occurrence)


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
        422: {
            "description": "Новое время вне часов работы школы либо не совпадает "
            "ни с одним слотом расписания"
        },
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
    responses={
        409: {"description": "Время занято другим активным занятием ученика"},
        422: {
            "description": "Время вне часов работы школы либо не совпадает "
            "ни с одним слотом расписания"
        },
    },
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
