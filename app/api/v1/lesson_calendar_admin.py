"""
Admin API Календаря LMS (tsk-428/435): часы работы школы + групповые слоты
расписания + их участники.

Гейт: роль ``admin`` (или сервисный токен) — расписание создаётся
централизованно оператором, не самим преподавателем/учеником (требование
оператора, см. docs/specs/2026-07-26-plan-kalendar-lms.md).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.repos.lesson_calendar_repository import LessonSlotStudentRepository
from app.schemas.lesson_calendar import (
    AddSlotParticipantRequest,
    LessonSlotCreate,
    LessonSlotRead,
    LessonSlotUpdate,
    OperatingHoursCreate,
    OperatingHoursRead,
    SlotParticipantRead,
)
from app.services import lesson_calendar_service

router = APIRouter(tags=["lesson_calendar_admin"])

_ADMIN_GATE = require_role("admin")
_slot_student_repo = LessonSlotStudentRepository()


async def _to_slot_read(db: AsyncSession, slot) -> LessonSlotRead:
    participants = await _slot_student_repo.list_for_slot(db, slot.id)
    data = LessonSlotRead.model_validate(slot).model_dump()
    data["student_ids"] = [p.student_id for p in participants]
    return LessonSlotRead(**data)


# ─── Operating Hours ────────────────────────────────────────────────────────


@router.get("/operating-hours", response_model=list[OperatingHoursRead])
async def get_operating_hours(
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> list[OperatingHoursRead]:
    rows = await lesson_calendar_service.list_operating_hours(db)
    return [OperatingHoursRead.model_validate(r) for r in rows]


@router.put(
    "/operating-hours",
    response_model=OperatingHoursRead,
    status_code=status.HTTP_200_OK,
    summary="Задать часы работы школы на день недели (заменяет существующую запись)",
)
async def put_operating_hours(
    body: OperatingHoursCreate = Body(...),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> OperatingHoursRead:
    row = await lesson_calendar_service.upsert_operating_hours(
        db,
        weekday=body.weekday,
        start_time=body.start_time,
        end_time=body.end_time,
        timezone=body.timezone,
    )
    return OperatingHoursRead.model_validate(row)


# ─── Lesson Slot (групповой) ────────────────────────────────────────────────


@router.post(
    "/lesson-slots",
    response_model=LessonSlotRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_lesson_slot(
    body: LessonSlotCreate = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> LessonSlotRead:
    row = await lesson_calendar_service.create_lesson_slot(
        db,
        teacher_id=body.teacher_id,
        weekday=body.weekday,
        start_time=body.start_time,
        duration_minutes=body.duration_minutes,
        timezone=body.timezone,
        created_by=current_user.id if not current_user.is_service else None,
        student_ids=body.student_ids,
    )
    return await _to_slot_read(db, row)


@router.get("/lesson-slots", response_model=list[LessonSlotRead])
async def list_lesson_slots(
    teacher_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> list[LessonSlotRead]:
    rows = await lesson_calendar_service.list_lesson_slots(db, teacher_id=teacher_id)
    return [await _to_slot_read(db, r) for r in rows]


@router.get("/lesson-slots/{slot_id}", response_model=LessonSlotRead)
async def get_lesson_slot(
    slot_id: int,
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> LessonSlotRead:
    row = await lesson_calendar_service.get_lesson_slot(db, slot_id)
    return await _to_slot_read(db, row)


@router.patch("/lesson-slots/{slot_id}", response_model=LessonSlotRead)
async def update_lesson_slot(
    slot_id: int,
    body: LessonSlotUpdate = Body(...),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> LessonSlotRead:
    row = await lesson_calendar_service.update_lesson_slot(
        db,
        slot_id,
        weekday=body.weekday,
        start_time=body.start_time,
        duration_minutes=body.duration_minutes,
        timezone=body.timezone,
        is_active=body.is_active,
    )
    return await _to_slot_read(db, row)


@router.delete("/lesson-slots/{slot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_lesson_slot(
    slot_id: int,
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> Response:
    """Деактивация (``is_active=false``), не физическое удаление — сохраняет
    историю уже сгенерированных occurrence."""
    await lesson_calendar_service.deactivate_lesson_slot(db, slot_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/lesson-slots/{slot_id}/participants",
    response_model=SlotParticipantRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_slot_participant(
    slot_id: int,
    body: AddSlotParticipantRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> SlotParticipantRead:
    row = await lesson_calendar_service.add_slot_participant(
        db, slot_id, body.student_id,
        added_by=current_user.id if not current_user.is_service else None,
    )
    return SlotParticipantRead.model_validate(row)


@router.get("/lesson-slots/{slot_id}/participants", response_model=list[SlotParticipantRead])
async def list_slot_participants(
    slot_id: int,
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> list[SlotParticipantRead]:
    rows = await lesson_calendar_service.list_slot_participants(db, slot_id)
    return [SlotParticipantRead.model_validate(r) for r in rows]


@router.delete(
    "/lesson-slots/{slot_id}/participants/{student_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_slot_participant(
    slot_id: int,
    student_id: int,
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ADMIN_GATE),
) -> Response:
    await lesson_calendar_service.remove_slot_participant(db, slot_id, student_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
