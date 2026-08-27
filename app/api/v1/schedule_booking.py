"""API записи в свободные слоты и заявок методисту (tsk-674, фаза 3).

Ученик:
- ``GET  /me/schedule-slots``                  — свободные и частично свободные слоты
- ``POST /me/schedule-slots/{slot_id}/join``   — записаться
- ``POST /me/schedule-slots/request``          — «Не нашёл подходящее время»

Методист/админ:
- ``GET  /methodist/schedule-slot-requests``            — очередь заявок
- ``POST /methodist/schedule-slot-requests/{id}/resolve`` — разобрано

Гейт методистской половины — тот же, что у вёрстки и охвата опроса
(`methodist`/`admin`): слот остаётся распорядительным решением, преподаватель
сюда не входит.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_authenticated, require_role
from app.auth.current_user import CurrentUser
from app.schemas.schedule_booking import (
    BookableSlotsRead,
    ScheduleSlotRequestRead,
    SlotRequestList,
    SlotRequestResolve,
    SlotRequestWrite,
)
from app.services import schedule_booking_service

router = APIRouter(tags=["schedule_booking"])

_REQUESTS_GATE = require_role("methodist", "admin")


@router.get("/me/schedule-slots", response_model=BookableSlotsRead)
async def get_my_bookable_slots(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> BookableSlotsRead:
    """Куда ученик может записаться и куда уже записан.

    Слоты, набравшие потолок (десять человек), в ответ не попадают вовсе —
    предложить их означало бы обещать место, которого нет.
    """
    data = await schedule_booking_service.get_bookable(db, current_user.id)
    return BookableSlotsRead(**data)


@router.post("/me/schedule-slots/{slot_id}/join", response_model=BookableSlotsRead)
async def join_slot(
    slot_id: int = Path(..., ge=1),
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> BookableSlotsRead:
    """Записаться на занятие в это время.

    Все проверки — на сервере: между показом экрана и нажатием кнопки место
    мог занять другой человек. `DomainError` наверх не ловим: общий обработчик
    в `app/api/main.py` отдаёт его текст ученику как есть, а тексты здесь
    написаны для человека.
    """
    data = await schedule_booking_service.join_slot(db, current_user.id, slot_id)
    return BookableSlotsRead(**data)


@router.post("/me/schedule-slots/request", response_model=ScheduleSlotRequestRead)
async def request_other_time(
    body: SlotRequestWrite = Body(default_factory=SlotRequestWrite),
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> ScheduleSlotRequestRead:
    """«Не нашёл подходящее время» — заявка методисту с пожеланиями ученика."""
    return await schedule_booking_service.create_request(
        db, current_user.id, body.comment
    )


@router.get("/methodist/schedule-slot-requests", response_model=SlotRequestList)
async def list_slot_requests(
    status: str | None = Query(
        "open", description="open | resolved | all — по умолчанию только открытые"
    ),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_REQUESTS_GATE),
) -> SlotRequestList:
    """Очередь «просят другое время»: кто ждёт и что просил."""
    data = await schedule_booking_service.list_requests(
        db, status=None if status == "all" else status, limit=limit
    )
    return SlotRequestList(**data)


@router.post(
    "/methodist/schedule-slot-requests/{request_id}/resolve",
    response_model=ScheduleSlotRequestRead,
)
async def resolve_slot_request(
    request_id: int = Path(..., ge=1),
    body: SlotRequestResolve = Body(default_factory=SlotRequestResolve),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_REQUESTS_GATE),
) -> ScheduleSlotRequestRead:
    """Заявка разобрана: добавили слот, договорились или человек записался сам."""
    return await schedule_booking_service.resolve_request(
        db,
        request_id,
        resolution_note=body.resolution_note,
        resolved_by=None if current_user.is_service else current_user.id,
    )
