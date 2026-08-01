"""tsk-513 — перерывы ученика в кабинете методиста.

Перерывами распоряжается тот, кто ведёт расписание, — методист. Маркетолог видит
их последствие в начислении, но не заводит: иначе занятия начали бы гаснуть из
денежного кабинета, мимо того, кто отвечает за расписание.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, require_role
from app.db.session import get_async_db
from app.schemas.charge import BreakCreateRequest, BreakRead, BreakWriteRequest
from app.services import break_service, charge_service

router = APIRouter(prefix="/methodist", tags=["methodist_breaks"])

_BREAKS_ROLE_GATE = require_role("methodist", "admin")


async def _breaks_gate(
    current_user: CurrentUser = Depends(_BREAKS_ROLE_GATE),
) -> CurrentUser:
    """Роль методиста/админа И живой человек, не сервисный ключ.

    `require_role` пропускает сервисный токен без проверки роли — ради ботов.
    Здесь это не годится: перерыв гасит занятия и меняет деньги, и след «кто
    завёл» терялся бы вместе с проверкой.
    """
    if current_user.is_service:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Перерывы заводит пользователь, а не сервисный ключ",
        )
    return current_user


@router.get("/breaks", response_model=list[BreakRead], summary="Перерывы учеников")
async def list_breaks(
    student_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_breaks_gate),
) -> list[BreakRead]:
    rows = await break_service.list_breaks(db, student_id=student_id)
    return [BreakRead(**r) for r in rows]


@router.post(
    "/breaks",
    response_model=BreakRead,
    status_code=status.HTTP_201_CREATED,
    summary="Завести перерыв",
    description=(
        "Занятия ученика в эти дни гаснут (статус on_break), а начисление "
        "текущего месяца пересчитывается сразу."
    ),
)
async def create_break(
    body: BreakCreateRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_breaks_gate),
) -> BreakRead:
    if not await break_service.student_exists(db, body.student_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")

    break_id = await break_service.create_break(
        db,
        student_id=body.student_id,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
        note=body.note,
        created_by=current_user.id,
    )
    await _recalc(db, body.student_id, body.starts_on, body.ends_on)
    return await _reload(db, break_id)


@router.patch("/breaks/{break_id}", response_model=BreakRead, summary="Изменить перерыв")
async def update_break(
    break_id: int,
    body: BreakWriteRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_breaks_gate),
) -> BreakRead:
    before = await break_service.get_break(db, break_id)
    if before is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Перерыв не найден")

    student_id = await break_service.update_break(
        db,
        break_id=break_id,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
        note=body.note,
    )
    if student_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Перерыв не найден")

    # Пересчитываем и старые месяцы, и новые: перерыв мог уехать из августа в
    # сентябрь, и тогда обоим месяцам нужен пересчёт, а не только новому.
    await _recalc(db, student_id, before["starts_on"], before["ends_on"])
    await _recalc(db, student_id, body.starts_on, body.ends_on)
    return await _reload(db, break_id)


@router.delete(
    "/breaks/{break_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Снять перерыв",
    description="Погашенные им занятия возвращаются в расписание.",
)
async def delete_break(
    break_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_breaks_gate),
) -> None:
    before = await break_service.get_break(db, break_id)
    if before is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Перерыв не найден")
    student_id = await break_service.delete_break(db, break_id=break_id)
    if student_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Перерыв не найден")
    await _recalc(db, student_id, before["starts_on"], before["ends_on"])


async def _recalc(
    db: AsyncSession, student_id: int, starts_on: date, ends_on: date
) -> None:
    """Пересчитать каждый месяц, которого касается перерыв.

    Перерыв может лежать поперёк границы месяцев — пересчёт только текущего
    оставил бы соседний со старой суммой.
    """
    seen: set[date] = set()
    cursor = charge_service.month_start(starts_on)
    last = charge_service.month_start(ends_on)
    while cursor <= last and cursor not in seen:
        seen.add(cursor)
        await charge_service.recalculate_for_student(
            db, student_id=student_id, period=cursor
        )
        cursor = charge_service.next_month(cursor)


async def _reload(db: AsyncSession, break_id: int) -> BreakRead:
    row = await break_service.get_break(db, break_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Перерыв не найден")
    rows = await break_service.list_breaks(db, student_id=row["student_id"])
    found = next((r for r in rows if r["id"] == break_id), None)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Перерыв не найден")
    return BreakRead(**found)
