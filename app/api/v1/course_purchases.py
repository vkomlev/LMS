"""tsk-1254 — покупка курса целиком: кабинет ученика и экран маркетолога.

Продажа закрыта рубильником COURSE_PURCHASE_ENABLED (выключен по умолчанию):
без него ни кнопка ученика, ни отметка маркетолога покупку не заведут. Чтение
(список покупок, сроки доступа) открыто всегда — оно ничего не меняет.

Зачисление оплаты картой — не здесь, а по уведомлению ЮKassa
(`payments_gateway.yookassa_webhook`, ветка `purpose = course`).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, require_role
from app.core.config import Settings
from app.db.session import get_async_db
from app.schemas.payment import GatewayPaymentStart
from app.services import course_purchase_service, yookassa_service

logger = logging.getLogger(__name__)
settings = Settings()

router = APIRouter(tags=["course_purchases"])

_STAFF_GATE = require_role("marketer", "admin")

PayMethod = Literal["full", "bank_installments"]


class CoursePurchaseStartRequest(BaseModel):
    course_id: int
    tariff_id: int
    pay_method: PayMethod = "full"


class StaffCoursePurchaseRequest(BaseModel):
    student_id: int
    course_id: int
    tariff_id: int
    pay_method: PayMethod = "full"
    #: Сразу отметить оплату (деньги пришли переводом/через банк). Иначе покупка
    #: ждёт оплаты.
    mark_paid: bool = True
    paid_on: Optional[date] = None
    note: Optional[str] = Field(default=None, max_length=500)


class ExtendAccessRequest(BaseModel):
    student_id: int
    course_id: int
    months: int = Field(ge=1, le=120)


@router.get("/me/course-purchases", summary="Мои покупки курсов и сроки доступа")
async def my_course_purchases(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    return {
        "sales_enabled": course_purchase_service.sales_enabled(),
        "items": await course_purchase_service.list_for_student(db, student_id=current_user.id),
        # Предложения нужны кабинету только когда продажа открыта.
        "offers": (
            await course_purchase_service.list_offers(db, student_id=current_user.id)
            if course_purchase_service.sales_enabled()
            else []
        ),
    }


@router.post(
    "/me/course-purchases/gateway",
    response_model=GatewayPaymentStart,
    summary="Оплатить курс целиком картой или долями через банк",
    responses={403: {"description": "Продажа курса целиком выключена"},
               503: {"description": "Оплата картой выключена"}},
)
async def start_course_payment(
    body: CoursePurchaseStartRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> GatewayPaymentStart:
    """Завести покупку и платёж в ЮKassa. Доступ выдаётся по уведомлению.

    Доли через банк — это способ оплаты на странице ЮKassa (подключается в её
    кабинете): банк платит школе всю сумму сразу, поэтому здесь путь один.
    """
    if current_user.is_service:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Оплата принимается только от пользователя")
    if not yookassa_service.is_enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Оплата картой сейчас недоступна")
    purchase = await course_purchase_service.create_pending(
        db,
        student_id=current_user.id,
        course_id=body.course_id,
        tariff_id=body.tariff_id,
        pay_method=body.pay_method,
        created_by=current_user.id,
    )
    try:
        payment = await yookassa_service.create_payment(
            amount_minor=purchase["price_minor"],
            description=f"Курс целиком, доступ {purchase['access_months']} мес.",
            return_url=f"{settings.public_base_url}/me/payments",
            metadata={
                "purpose": course_purchase_service.PURPOSE_COURSE,
                "student_id": str(current_user.id),
                "course_purchase_id": str(purchase["id"]),
            },
            idempotence_key=f"course-purchase-{purchase['id']}",
        )
    except yookassa_service.GatewayDisabledError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except yookassa_service.GatewayError as exc:
        await db.rollback()
        logger.warning("tsk-1254: не удалось завести платёж за курс: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Платёжный сервис не ответил, попробуйте позже"
        ) from exc
    if payment.confirmation_url is None:
        await db.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Платёжный сервис не вернул ссылку")
    # Покупку фиксируем только когда платёж в шлюзе заведён: иначе в базе
    # копились бы «ждущие оплаты» покупки, которых не оплатить.
    await db.commit()
    return GatewayPaymentStart(
        payment_id=payment.id,
        confirmation_url=payment.confirmation_url,
        amount_minor=payment.amount_minor,
        test_mode=payment.test,
    )


@router.post(
    "/marketer/course-purchases",
    summary="Завести покупку курса за ученика (и отметить оплату)",
    dependencies=[Depends(_STAFF_GATE)],
)
async def staff_create_purchase(
    body: StaffCoursePurchaseRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_STAFF_GATE),
) -> dict:
    paid_on = _check_paid_on(body.paid_on)
    purchase = await course_purchase_service.create_pending(
        db,
        student_id=body.student_id,
        course_id=body.course_id,
        tariff_id=body.tariff_id,
        pay_method=body.pay_method,
        created_by=_staff_id(current_user),
        note=body.note,
    )
    await db.commit()
    if body.mark_paid:
        await course_purchase_service.mark_paid(
            db,
            purchase_id=purchase["id"],
            amount_minor=purchase["price_minor"],
            paid_on=paid_on,
            staff_id=_staff_id(current_user),
        )
    return {"id": purchase["id"], "paid": body.mark_paid}


@router.post(
    "/marketer/course-purchases/{purchase_id}/mark-paid",
    summary="Отметить оплату ждущей покупки",
    dependencies=[Depends(_STAFF_GATE)],
)
async def staff_mark_paid(
    purchase_id: int,
    paid_on: Optional[date] = None,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_STAFF_GATE),
) -> dict:
    if not course_purchase_service.sales_enabled():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Покупка курса целиком пока не включена")
    price = await _price_of(db, purchase_id)
    done = await course_purchase_service.mark_paid(
        db, purchase_id=purchase_id, amount_minor=price,
        paid_on=_check_paid_on(paid_on), staff_id=_staff_id(current_user),
    )
    return {"status": "recorded" if done else "already_paid"}


@router.get(
    "/marketer/students/{student_id}/course-purchases",
    summary="Покупки курсов ученика",
    dependencies=[Depends(_STAFF_GATE)],
)
async def staff_student_purchases(
    student_id: int, db: AsyncSession = Depends(get_async_db)
) -> dict:
    return {
        "sales_enabled": course_purchase_service.sales_enabled(),
        "items": await course_purchase_service.list_for_student(db, student_id=student_id),
        "offers": await course_purchase_service.list_offers(db, student_id=student_id),
    }


@router.post(
    "/marketer/course-access/extend",
    summary="Продлить доступ к курсу",
    dependencies=[Depends(_STAFF_GATE)],
)
async def staff_extend_access(
    body: ExtendAccessRequest, db: AsyncSession = Depends(get_async_db)
) -> dict:
    until = await course_purchase_service.extend_access(
        db, student_id=body.student_id, course_id=body.course_id, months=body.months
    )
    if until is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Продлевать нечего: ученик не зачислен или доступ бессрочный",
        )
    return {"access_until": until.isoformat()}


@router.get(
    "/marketer/course-access/expiring",
    summary="Доступ кончается в ближайшие дни",
    dependencies=[Depends(_STAFF_GATE)],
)
async def staff_expiring(
    within_days: int = 30, db: AsyncSession = Depends(get_async_db)
) -> list[dict]:
    rows = await course_purchase_service.list_expiring(db, within_days=within_days)
    # Почту на экран маркетолога не отдаём — она нужна только рассылке.
    for r in rows:
        r.pop("email", None)
    return rows


def _staff_id(user: CurrentUser) -> Optional[int]:
    """Автор отметки: у сервисного ключа учётки нет — пишем пусто, не выдуманный id."""
    return None if user.is_service else user.id


def _check_paid_on(paid_on: Optional[date]) -> date:
    """Дата оплаты от маркетолога — только текущий месяц и не будущее.

    Задним числом покупка сняла бы с помесячного счёта прошлые, возможно уже
    оплаченные, месяцы, а пересчитывается только текущий — переплата осталась
    бы невидимой.
    """
    today = date.today()
    day = paid_on or today
    if day > today or day < today.replace(day=1):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Дата оплаты — только в текущем месяце и не позже сегодня",
        )
    return day


class RefundRequest(BaseModel):
    note: Optional[str] = Field(default=None, max_length=500)


@router.post(
    "/marketer/course-purchases/{purchase_id}/refund",
    summary="Отметить возврат за курс (деньги возвращены вручную)",
    dependencies=[Depends(_STAFF_GATE)],
)
async def staff_refund(
    purchase_id: int,
    body: RefundRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_STAFF_GATE),
) -> dict:
    done = await course_purchase_service.refund(
        db, purchase_id=purchase_id, staff_id=_staff_id(current_user), note=body.note
    )
    if not done:
        raise HTTPException(status.HTTP_409_CONFLICT, "Покупка не найдена или не оплачена")
    return {"status": "refunded"}


async def _price_of(db: AsyncSession, purchase_id: int) -> int:
    from sqlalchemy import text

    row = (
        await db.execute(
            text("SELECT price_minor FROM course_purchase WHERE id = :id"), {"id": purchase_id}
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Покупка не найдена")
    return int(row.price_minor)
