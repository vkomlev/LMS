"""API пожеланий по расписанию (tsk-674, фаза 1).

Ученик:
- ``GET  /me/schedule-preference``          — что выбрано + сама сетка часов
- ``PUT  /me/schedule-preference``          — сохранить (перезапись целиком)
- ``GET  /me/schedule-preference/history``  — история собственных правок

Методист/админ:
- ``GET  /methodist/schedule-preferences/summary``            — охват опроса
- ``GET  /methodist/schedule-preferences/{student_id}``       — пожелание ученика
- ``GET  /methodist/schedule-preferences/{student_id}/history`` — его история

Гейт сводки — тот же, что у расписания (`methodist`/`admin`): вёрстку делает
методист, и охват опроса нужен ему же. Преподаватель сюда не входит по той же
причине, что и в `lesson_calendar_admin`: слот — распорядительное решение.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_authenticated, require_role
from app.auth.current_user import CurrentUser
from app.schemas.schedule_preference import (
    SchedulePreferenceRead,
    SchedulePreferenceRevisionRead,
    SchedulePreferenceSummary,
    SchedulePreferenceWrite,
)
from app.services import schedule_preference_service
from app.services.schedule_preference_service import SchedulePreferenceError

router = APIRouter(tags=["schedule_preferences"])

_SUMMARY_GATE = require_role("methodist", "admin")


@router.get("/me/schedule-preference", response_model=SchedulePreferenceRead)
async def get_my_schedule_preference(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> SchedulePreferenceRead:
    """Пожелания текущего ученика вместе с сеткой допустимых часов.

    Ответ отдаётся и тому, кто в аудиторию опроса не входит (выпускник, демо):
    поле `is_audience=false` — это ответ «опрос не для вас», а не отказ. Отказ
    пришлось бы объяснять на экране, а объяснять тут нечего.
    """
    data = await schedule_preference_service.get_preference(db, current_user.id)
    return SchedulePreferenceRead(**data)


@router.put("/me/schedule-preference", response_model=SchedulePreferenceRead)
async def save_my_schedule_preference(
    body: SchedulePreferenceWrite,
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> SchedulePreferenceRead:
    """Сохранить пожелания. Правится в любой момент, каждая версия остаётся в истории."""
    try:
        data = await schedule_preference_service.save_preference(
            db, current_user.id, body, changed_by=current_user.id
        )
    except SchedulePreferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return SchedulePreferenceRead(**data)


@router.get(
    "/me/schedule-preference/history",
    response_model=list[SchedulePreferenceRevisionRead],
)
async def get_my_schedule_preference_history(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> list[SchedulePreferenceRevisionRead]:
    rows = await schedule_preference_service.list_history(db, current_user.id)
    return [SchedulePreferenceRevisionRead(**r) for r in rows]


@router.get(
    "/methodist/schedule-preferences/summary",
    response_model=SchedulePreferenceSummary,
)
async def get_schedule_preferences_summary(
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_SUMMARY_GATE),
) -> SchedulePreferenceSummary:
    """Охват опроса: сколько заполнили, кто молчит, какой час сколько просят."""
    return SchedulePreferenceSummary(**await schedule_preference_service.get_summary(db))


@router.get(
    "/methodist/schedule-preferences/{student_id}",
    response_model=SchedulePreferenceRead,
)
async def get_student_schedule_preference(
    student_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_SUMMARY_GATE),
) -> SchedulePreferenceRead:
    data = await schedule_preference_service.get_preference(db, student_id)
    return SchedulePreferenceRead(**data)


@router.get(
    "/methodist/schedule-preferences/{student_id}/history",
    response_model=list[SchedulePreferenceRevisionRead],
)
async def get_student_schedule_preference_history(
    student_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_SUMMARY_GATE),
) -> list[SchedulePreferenceRevisionRead]:
    rows = await schedule_preference_service.list_history(db, student_id)
    return [SchedulePreferenceRevisionRead(**r) for r in rows]
