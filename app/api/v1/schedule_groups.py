"""
Справочник групп расписания и группы ученика (tsk-1124).

Чтение справочника — методист, админ и преподаватель (фильтр в кабинете
преподавателя). Правка справочника и групп ученика — методист и админ: группа
решает, какие слоты ученик видит, это распорядительное решение методиста.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.schemas.schedule_group import (
    ScheduleGroupCreate,
    ScheduleGroupRead,
    ScheduleGroupUpdate,
    StudentScheduleGroupsRead,
    StudentScheduleGroupsUpdate,
)
from app.services import lesson_calendar_service, schedule_group_service

router = APIRouter(prefix="/schedule-groups", tags=["schedule_groups"])

_READ_GATE = require_role("methodist", "admin", "teacher")
_WRITE_GATE = require_role("methodist", "admin")


@router.get("", response_model=list[ScheduleGroupRead])
async def list_schedule_groups(
    include_inactive: bool = Query(default=False),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_READ_GATE),
) -> list[ScheduleGroupRead]:
    rows = await schedule_group_service.list_groups(db, include_inactive=include_inactive)
    return [ScheduleGroupRead.model_validate(r) for r in rows]


@router.post("", response_model=ScheduleGroupRead, status_code=status.HTTP_201_CREATED)
async def create_schedule_group(
    body: ScheduleGroupCreate = Body(...),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_WRITE_GATE),
) -> ScheduleGroupRead:
    row = await schedule_group_service.create_group(
        db, audience=body.audience, subject=body.subject, name=body.name,
        pricing_group_id=body.pricing_group_id,
    )
    return ScheduleGroupRead.model_validate(row)


@router.patch("/{group_id}", response_model=ScheduleGroupRead)
async def update_schedule_group(
    group_id: int,
    body: ScheduleGroupUpdate = Body(...),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_WRITE_GATE),
) -> ScheduleGroupRead:
    row = await schedule_group_service.update_group(
        db, group_id,
        audience=body.audience, subject=body.subject, name=body.name,
        pricing_group_id=body.pricing_group_id,
        clear_pricing_group=body.clear_pricing_group,
        is_active=body.is_active,
    )
    return ScheduleGroupRead.model_validate(row)


@router.get("/students/{student_id}", response_model=StudentScheduleGroupsRead)
async def get_student_schedule_groups(
    student_id: int,
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_WRITE_GATE),
) -> StudentScheduleGroupsRead:
    # Как у PUT: несуществующий или не-ученик — 404/422, а не «группа по умолчанию».
    await lesson_calendar_service.ensure_user_has_role(db, student_id, "student")
    return StudentScheduleGroupsRead(
        student_id=student_id,
        group_ids=await schedule_group_service.explicit_group_ids(db, student_id),
        effective_group_ids=await schedule_group_service.effective_group_ids(db, student_id),
    )


@router.put("/students/{student_id}", response_model=StudentScheduleGroupsRead)
async def set_student_schedule_groups(
    student_id: int,
    body: StudentScheduleGroupsUpdate = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_WRITE_GATE),
) -> StudentScheduleGroupsRead:
    explicit = await schedule_group_service.set_student_groups(
        db, student_id, body.group_ids,
        added_by=None if current_user.is_service else current_user.id,
    )
    return StudentScheduleGroupsRead(
        student_id=student_id,
        group_ids=explicit,
        effective_group_ids=await schedule_group_service.effective_group_ids(db, student_id),
    )
