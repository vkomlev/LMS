"""tsk-010 — ручная отметка оплаты в карточке ученика.

Нужна для двух живых случаев, которые система иначе не покрывает: месяц уже
оплатили до того, как она появилась, и человек не разобрался с кабинетом, а
деньги прислал. В обоих случаях чека в системе нет и не будет — подтверждать
нечего, решение принимает тот, кто отмечает.

Поэтому платёж пишется сразу подтверждённым, а автором записан отметивший:
через полгода примечание и его имя будут единственным объяснением, откуда
взялись эти деньги.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, require_role
from app.db.session import get_async_db
from app.schemas.payment import StudentChargeRead
from app.services import payment_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/staff/students", tags=["staff_payments"])

# Методист ведёт учеников и первым узнаёт, что оплата пришла мимо системы.
# Маркетолог и админ — денежный контур целиком.
_STAFF_GATE = require_role("methodist", "marketer", "admin")


async def _staff(current_user: CurrentUser = Depends(_STAFF_GATE)) -> CurrentUser:
    """Денежный контур закрыт для сервисного ключа — как и везде в tsk-010."""
    if current_user.is_service:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Отметка оплаты доступна только пользователю, не сервисному ключу",
        )
    return current_user


class StaffPaymentCreate(BaseModel):
    """Ручная отметка: за какой месяц, сколько и почему без чека."""

    charge_id: int
    amount_minor: int = Field(..., gt=0)
    paid_on: Optional[date] = None
    #: Обязательное: платёж без чека нечем подтвердить, кроме этой строки.
    note: str = Field(..., min_length=3, max_length=500)


@router.get(
    "/{student_id}/charges",
    response_model=list[StudentChargeRead],
    summary="Начисления и оплаты ученика",
    description="Для карточки ученика: что начислено, что уже пришло, что осталось.",
)
async def student_charges(
    student_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_staff),
) -> list[StudentChargeRead]:
    rows = await payment_service.list_student_charges(db, student_id=student_id)
    return [StudentChargeRead(**r) for r in rows]


@router.post(
    "/{student_id}/payments",
    status_code=status.HTTP_201_CREATED,
    summary="Отметить оплату вручную",
    description=(
        "Платёж сразу подтверждён: чека нет, подтверждать нечего. Автор — тот, "
        "кто отмечает; примечание обязательно."
    ),
)
async def record_payment(
    student_id: int,
    body: StaffPaymentCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_staff),
) -> dict:
    charge = await payment_service.charge_for_student(
        db, charge_id=body.charge_id, student_id=student_id
    )
    if charge is None:
        # Начисление чужого ученика и несуществующее — один ответ: карточка
        # открыта по одному человеку, и отметить оплату «соседу» нельзя даже
        # случайно, перепутав номер.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Начисление не найдено")

    if body.paid_on is not None and body.paid_on > date.today():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Дата платежа не может быть в будущем"
        )

    payment_id = await payment_service.record_staff_payment(
        db,
        student_id=student_id,
        group_id=charge["group_id"],
        period=charge["period"],
        amount_minor=body.amount_minor,
        paid_on=body.paid_on,
        note=body.note.strip(),
        recorded_by=current_user.id,
    )
    return {"id": payment_id, "status": "confirmed"}
