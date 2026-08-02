"""tsk-010 — оплата картой: создание платежа и приём уведомлений от шлюза.

Уведомление приходит от чужого сервера, без нашей сессии, поэтому адрес
уведомлений открыт. Защита не в том, кто постучался, а в том, что зачисление
идёт ТОЛЬКО по перезапросу платежа у шлюза: пустой POST-запрос от постороннего
ничего не оплатит, потому что такого платежа у шлюза нет.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.core.config import Settings
from app.db.session import get_async_db
from app.schemas.payment import GatewayPaymentStart, PaymentStartRequest
from app.services import payment_service, yookassa_service

logger = logging.getLogger(__name__)
settings = Settings()

router = APIRouter(tags=["payments_gateway"])

_GATEWAY = "yookassa"


@router.get(
    "/me/payments/gateway-status",
    summary="Доступна ли оплата картой",
    description="Кабинет спрашивает до показа кнопки: способ может быть не настроен.",
)
async def gateway_status(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    return {
        "enabled": yookassa_service.is_enabled(),
        # Тестовый режим показываем открыто: платящий должен понимать, что
        # деньги не спишутся, а не гадать, почему карта «не прошла».
        "test_mode": yookassa_service.is_test_mode(),
        # Реквизиты перевода — основного способа оплаты.
        "transfer_details": settings.payment_transfer_details or None,
    }


@router.post(
    "/me/payments/gateway",
    response_model=GatewayPaymentStart,
    summary="Оплатить картой",
    description=(
        "Заводит платёж в платёжном сервисе и возвращает ссылку, по которой "
        "плательщик вводит данные карты. Деньги зачисляются не здесь, а по "
        "уведомлению от сервиса."
    ),
)
async def start_gateway_payment(
    body: PaymentStartRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> GatewayPaymentStart:
    if current_user.is_service:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Оплата принимается только от пользователя, не от сервисного ключа",
        )
    if not yookassa_service.is_enabled():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Оплата картой сейчас недоступна — приложите чек об оплате переводом",
        )

    charge = await _resolve_own_charge(db, current_user, body.charge_id)
    total = await _charge_due_minor(db, charge)
    amount = body.amount_minor or total
    if amount <= 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "По этому месяцу платить нечего — он уже оплачен"
        )
    if amount > total and total > 0:
        # Переплату через шлюз не заводим: вернуть её сложнее, чем не принять.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Сумма больше остатка по месяцу",
        )

    try:
        payment = await yookassa_service.create_payment(
            amount_minor=amount,
            description=f"Обучение, {charge['period']:%m.%Y}",
            return_url=f"{settings.public_base_url}/me/payments",
            metadata={
                "charge_id": str(charge["id"]),
                "student_id": str(charge["student_id"]),
            },
        )
    except yookassa_service.GatewayDisabledError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except yookassa_service.GatewayError as exc:
        logger.warning("tsk-010: не удалось завести платёж в шлюзе: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Платёжный сервис не ответил, попробуйте позже или приложите чек",
        ) from exc

    if payment.confirmation_url is None:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Платёжный сервис не вернул ссылку на оплату"
        )
    return GatewayPaymentStart(
        payment_id=payment.id,
        confirmation_url=payment.confirmation_url,
        amount_minor=payment.amount_minor,
        test_mode=payment.test,
    )


@router.post(
    "/payments/yookassa/webhook",
    status_code=status.HTTP_200_OK,
    summary="Уведомление платёжного сервиса",
    description=(
        "Открытый адрес: сервис стучится без нашей сессии. Телу уведомления не "
        "верим — статус платежа перезапрашивается у сервиса."
    ),
)
async def yookassa_webhook(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        # Отвечаем 200: невнятное тело — не повод заставлять сервис повторять
        # доставку сутки подряд.
        logger.warning("tsk-010: уведомление шлюза с нечитаемым телом")
        return {"status": "ignored"}

    obj = payload.get("object") or {}
    payment_id = str(obj.get("id") or "").strip()
    if not payment_id:
        logger.warning("tsk-010: уведомление шлюза без номера платежа")
        return {"status": "ignored"}

    try:
        payment = await yookassa_service.fetch_payment(payment_id)
    except yookassa_service.GatewayDisabledError:
        logger.error("tsk-010: уведомление пришло, а оплата картой выключена")
        return {"status": "ignored"}
    except yookassa_service.GatewayError as exc:
        # Здесь 502: пусть сервис повторит доставку, когда связь восстановится.
        # Молча проглотить значит потерять деньги, которые уже списаны.
        logger.error("tsk-010: не удалось сверить платёж %s: %s", payment_id, exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Не удалось сверить платёж, повторите доставку"
        ) from exc

    if payment.status != "succeeded" or not payment.paid:
        logger.info(
            "tsk-010: платёж %s в статусе %s — зачислять нечего", payment_id, payment.status
        )
        return {"status": "skipped", "payment_status": payment.status}

    # Данные берём из ОТВЕТА шлюза, а не из тела уведомления: тело мог прислать
    # кто угодно, ответ приходит по нашему запросу с нашими ключами.
    charge_id = _as_int(payment.metadata.get("charge_id"))
    if charge_id is None:
        logger.error("tsk-010: платёж %s без ссылки на начисление", payment_id)
        return {"status": "ignored"}

    charge = await payment_service.charge_by_id(db, charge_id=charge_id)
    if charge is None:
        logger.error(
            "tsk-010: платёж %s ссылается на несуществующее начисление %s",
            payment_id,
            charge_id,
        )
        return {"status": "ignored"}

    created = await payment_service.record_gateway_payment(
        db,
        student_id=charge["student_id"],
        group_id=charge["group_id"],
        period=charge["period"],
        amount_minor=payment.amount_minor,
        gateway=_GATEWAY,
        gateway_payment_id=payment.id,
        paid_on=date.today(),
    )
    return {"status": "recorded" if created else "already_recorded"}


async def _resolve_own_charge(
    db: AsyncSession, current_user: CurrentUser, charge_id: int
) -> dict:
    """Начисление платящего: своё либо своего ребёнка."""
    charge = await payment_service.charge_for_student(
        db, charge_id=charge_id, student_id=current_user.id
    )
    if charge is None:
        for child_id in await payment_service.student_ids_for_parent(
            db, parent_id=current_user.id
        ):
            charge = await payment_service.charge_for_student(
                db, charge_id=charge_id, student_id=child_id
            )
            if charge is not None:
                break
    if charge is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Начисление не найдено")
    return charge


async def _charge_due_minor(db: AsyncSession, charge: dict) -> int:
    """Сколько осталось заплатить по этому месяцу."""
    rows = await payment_service.list_student_charges(
        db, student_id=charge["student_id"]
    )
    found = next((r for r in rows if r["id"] == charge["id"]), None)
    return int(found["due_minor"]) if found else 0


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
