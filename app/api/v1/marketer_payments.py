"""tsk-010 — очередь подтверждения оплат в кабинете маркетолога."""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, require_role
from app.api.v1.me_payments import receipt_response
from app.core.config import Settings
from app.db.session import get_async_db
from app.schemas.payment import (
    PaymentDecisionRequest,
    PaymentExportRow,
    PaymentRead,
    PaymentStatus,
)
from app.services import payment_service

logger = logging.getLogger(__name__)
settings = Settings()

router = APIRouter(prefix="/marketer", tags=["marketer_payments"])

_PAYMENTS_ROLE_GATE = require_role("marketer", "admin")


async def _payments_gate(
    current_user: CurrentUser = Depends(_PAYMENTS_ROLE_GATE),
) -> CurrentUser:
    """Денежный контур закрыт для сервисного ключа — как в начислениях и ценах."""
    if current_user.is_service:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Кабинет маркетолога доступен только пользователю, не сервисному ключу",
        )
    return current_user


@router.get(
    "/payments",
    response_model=list[PaymentRead],
    summary="Платежи: очередь на подтверждение и история",
    description="Без фильтра сверху идут платежи, ждущие решения.",
)
async def list_payments(
    payment_status: Optional[PaymentStatus] = Query(default=None, alias="status"),
    period: Optional[date] = Query(default=None),
    student_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_payments_gate),
) -> list[PaymentRead]:
    rows = await payment_service.list_payments(
        db, status=payment_status, period=period, student_id=student_id
    )
    return [PaymentRead(**r) for r in rows]


@router.post(
    "/payments/{payment_id}/confirm",
    response_model=PaymentRead,
    summary="Подтвердить платёж",
    description=(
        "Деньги считаются полученными. Налоговый чек в «Мой налог» система не "
        "выбивает — его оператор выдаёт сам, для сверки есть выгрузка по датам."
    ),
)
async def confirm(
    payment_id: int,
    body: PaymentDecisionRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_payments_gate),
) -> PaymentRead:
    decided = await payment_service.confirm_payment(
        db, payment_id=payment_id, reviewed_by=current_user.id, note=body.note
    )
    if decided is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Платёж не найден или решение по нему уже принято"
        )
    return await _reload(db, payment_id)


@router.post(
    "/payments/{payment_id}/reject",
    response_model=PaymentRead,
    summary="Отклонить платёж",
)
async def reject(
    payment_id: int,
    body: PaymentDecisionRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_payments_gate),
) -> PaymentRead:
    decided = await payment_service.reject_payment(
        db, payment_id=payment_id, reviewed_by=current_user.id, note=body.note
    )
    if decided is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Платёж не найден или решение по нему уже принято"
        )
    return await _reload(db, payment_id)


@router.get(
    "/payments/{payment_id}/receipt",
    summary="Посмотреть чек",
)
async def download_receipt(
    payment_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_payments_gate),
) -> FileResponse:
    payment = await payment_service.get_receipt(db, payment_id=payment_id)
    if payment is None or payment["receipt_file"] is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Чек не найден")
    # Общая выдача файла с типом по имени НА ДИСКЕ — см. `me_payments`:
    # присланное имя доверять нельзя, иначе `evil.svg` вернётся активным.
    return receipt_response(payment)


@router.get(
    "/payments/export",
    response_model=list[PaymentExportRow],
    summary="Выгрузка подтверждённых платежей за период",
    description=(
        "Для сверки с чеками, выбитыми в «Мой налог» вручную. Дата — день "
        "платежа, а не день подтверждения."
    ),
)
async def export(
    date_from: date = Query(...),
    date_to: date = Query(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_payments_gate),
) -> list[PaymentExportRow]:
    if date_to < date_from:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Конец периода раньше начала"
        )
    rows = await payment_service.export_confirmed(
        db, date_from=date_from, date_to=date_to
    )
    return [PaymentExportRow(**r) for r in rows]


async def _reload(db: AsyncSession, payment_id: int) -> PaymentRead:
    """Перечитать платёж после решения — отдаём то, что реально в базе.

    Фильтр по номеру идёт в SQL, а не постфильтром по всему списку платежей:
    иначе каждое решение вычитывало бы таблицу целиком.
    """
    rows = await payment_service.list_payments(db, payment_id=payment_id)
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Платёж не найден")
    return PaymentRead(**rows[0])
