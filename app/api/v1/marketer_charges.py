"""tsk-511/512 — начисления за месяц и ручная цена в кабинете маркетолога."""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, require_role
from app.db.session import get_async_db
from app.schemas.charge import (
    BlockHoldRead,
    BlockHoldRequest,
    ChargeRead,
    ClosePeriodRequest,
    ManualAmountRequest,
    PriceOverrideRead,
    PriceOverrideRequest,
    RecalculateResult,
)
from app.services import (
    break_service,
    charge_service,
    payment_block_hold_service,
    payment_service,
)

router = APIRouter(prefix="/marketer", tags=["marketer_charges"])

_CHARGES_ROLE_GATE = require_role("marketer", "admin")


async def _charges_gate(
    current_user: CurrentUser = Depends(_CHARGES_ROLE_GATE),
) -> CurrentUser:
    """См. `marketer_pricing`: денежный контур закрыт для сервисного ключа."""
    if current_user.is_service:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Кабинет маркетолога доступен только пользователю, не сервисному ключу",
        )
    return current_user


def _resolve_period(period: Optional[date]) -> date:
    return charge_service.month_start(period or date.today())


@router.get(
    "/charges",
    response_model=list[ChargeRead],
    summary="Начисления за месяц",
    description=(
        "Каждая строка показывает не только итог, но и из чего он сложился: "
        "расчёт, ручная сумма, переносы с прошлых месяцев, сколько занятий "
        "предполагал месяц и сколько съел перерыв."
    ),
)
async def list_charges(
    period: Optional[date] = Query(default=None),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_charges_gate),
) -> list[ChargeRead]:
    target = _resolve_period(period)
    rows = await charge_service.list_charges(db, period=target)
    # tsk-010: рядом с суммой месяца — что по ней уже пришло. Начисление и
    # оплата остаются разными слоями: расчёт не знает о платежах, платежи
    # дописываются поверх готовых строк.
    rows = await payment_service.attach_payment_state(db, rows, period=target)
    return [ChargeRead(**r) for r in rows]


@router.post(
    "/charges/recalculate",
    response_model=RecalculateResult,
    summary="Пересчитать месяц",
    description=(
        "Открытые месяцы переписываются. Закрытые не трогаются — расхождение "
        "уходит поправкой в следующий открытый месяц."
    ),
)
async def recalculate(
    period: Optional[date] = Query(default=None),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_charges_gate),
) -> RecalculateResult:
    target = _resolve_period(period)
    touched = await charge_service.recalculate_month(db, period=target)
    return RecalculateResult(period=target, touched=touched)


@router.put(
    "/charges/{charge_id}/manual",
    response_model=ChargeRead,
    summary="Поставить сумму месяца руками",
)
async def set_manual(
    charge_id: int,
    body: ManualAmountRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_charges_gate),
) -> ChargeRead:
    if not await charge_service.set_manual_amount(
        db, charge_id=charge_id, amount_minor=body.amount_minor
    ):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Начисление не найдено или месяц уже закрыт",
        )
    return await _reload_charge(db, charge_id)


@router.delete(
    "/charges/{charge_id}/manual",
    response_model=ChargeRead,
    summary="Вернуть месяц к расчёту",
)
async def clear_manual(
    charge_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_charges_gate),
) -> ChargeRead:
    if not await charge_service.clear_manual_amount(db, charge_id=charge_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Начисление не найдено или месяц уже закрыт",
        )
    return await _reload_charge(db, charge_id)


@router.post(
    "/charges/close",
    response_model=RecalculateResult,
    summary="Закрыть месяц",
    description="Суммы замирают; дальнейшие расхождения идут переносом в следующий месяц.",
)
async def close_period(
    body: ClosePeriodRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_charges_gate),
) -> RecalculateResult:
    closed = await charge_service.close_month(
        db, period=body.period, closed_by=current_user.id
    )
    return RecalculateResult(period=body.period, touched=closed)


@router.post("/charges/reopen", response_model=RecalculateResult, summary="Открыть месяц обратно")
async def reopen_period(
    body: ClosePeriodRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_charges_gate),
) -> RecalculateResult:
    reopened = await charge_service.reopen_month(db, period=body.period)
    return RecalculateResult(period=body.period, touched=reopened)


@router.get(
    "/price-overrides",
    response_model=list[PriceOverrideRead],
    summary="Ручные цены учеников",
)
async def list_overrides(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_charges_gate),
) -> list[PriceOverrideRead]:
    rows = await charge_service.list_overrides(db)
    return [PriceOverrideRead(**r) for r in rows]


@router.put(
    "/price-overrides",
    response_model=list[PriceOverrideRead],
    summary="Назначить ученику цену руками",
    description=(
        "Держится и при смене расписания: поставили руками — значит, была "
        "причина. Расхождение с расчётом видно на экране начислений."
    ),
)
async def set_override(
    body: PriceOverrideRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_charges_gate),
) -> list[PriceOverrideRead]:
    # Тот же гейт персональных данных, что и у привязки лида: принимаем только
    # действующего ученика, иначе перебором номеров всплывут чужие имена.
    if not await break_service.student_exists(db, body.student_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
    try:
        await charge_service.set_price_override(
            db,
            student_id=body.student_id,
            group_id=body.group_id,
            price_minor=body.price_minor,
            note=body.note,
            created_by=current_user.id,
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Тарифная группа не найдена"
        ) from exc
    await charge_service.recalculate_open_months_for_student(
        db, student_id=body.student_id
    )
    rows = await charge_service.list_overrides(db)
    return [PriceOverrideRead(**r) for r in rows]


@router.delete(
    "/price-overrides/{student_id}/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Снять ручную цену",
)
async def clear_override(
    student_id: int,
    group_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_charges_gate),
) -> None:
    if not await charge_service.clear_price_override(
        db, student_id=student_id, group_id=group_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ручная цена не найдена")
    await charge_service.recalculate_open_months_for_student(
        db, student_id=student_id
    )


# ── tsk-744: отсрочка блокировки за неоплату ────────────────────────────────


@router.get(
    "/block-holds",
    response_model=list[BlockHoldRead],
    summary="Отсрочки блокировки за неоплату",
    description=(
        "По умолчанию — только действующие. `only_active=false` открывает "
        "историю: сколько раз ученику уже шли навстречу."
    ),
)
async def list_block_holds(
    student_id: Optional[int] = Query(default=None),
    only_active: bool = Query(default=True),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_charges_gate),
) -> list[BlockHoldRead]:
    holds = await payment_block_hold_service.list_holds(
        db, student_id=student_id, only_active=only_active
    )
    return [BlockHoldRead(**vars(h)) for h in holds]


@router.post(
    "/block-holds",
    response_model=BlockHoldRead,
    status_code=status.HTTP_201_CREATED,
    summary="Отложить блокировку ученику",
    description=(
        "Долг не гасится и с экрана оплаты не исчезает — откладывается только "
        "закрытие занятий. Срок обязателен и истекает сам; прежняя действующая "
        "отсрочка этого ученика снимается, чтобы срок можно было и сократить."
    ),
)
async def create_block_hold(
    body: BlockHoldRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_charges_gate),
) -> BlockHoldRead:
    # Тот же гейт персональных данных, что у ручной цены: принимаем только
    # действующего ученика, иначе перебором номеров всплывут чужие имена.
    if not await break_service.student_exists(db, body.student_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
    hold = await payment_block_hold_service.create_hold(
        db,
        student_id=body.student_id,
        until=body.until,
        reason=body.reason,
        created_by=current_user.id,
    )
    return BlockHoldRead(**vars(hold))


@router.delete(
    "/block-holds/{hold_id}",
    response_model=BlockHoldRead,
    summary="Снять отсрочку досрочно",
    description=(
        "Строка остаётся в истории — снимается только действие. Блокировка "
        "возвращается к общему правилу со следующего же запроса."
    ),
)
async def cancel_block_hold(
    hold_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_charges_gate),
) -> BlockHoldRead:
    hold = await payment_block_hold_service.cancel_hold(
        db, hold_id=hold_id, cancelled_by=current_user.id
    )
    if hold is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Действующая отсрочка не найдена"
        )
    return BlockHoldRead(**vars(hold))


async def _reload_charge(db: AsyncSession, charge_id: int) -> ChargeRead:
    """Перечитать начисление после записи.

    Явная ошибка вместо `assert`: под `python -O` проверки-assert исчезают, а это
    денежный контур — молчаливый None в ответе недопустим.
    """
    row = (
        await db.execute(
            text("SELECT period FROM student_monthly_charge WHERE id = :id"),
            {"id": charge_id},
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Начисление не найдено")
    rows = await charge_service.list_charges(db, period=row.period)
    rows = await payment_service.attach_payment_state(db, rows, period=row.period)
    found = next((r for r in rows if r["id"] == charge_id), None)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Начисление не найдено")
    return ChargeRead(**found)
