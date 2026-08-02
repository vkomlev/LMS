"""tsk-010 — оплата родителем по гостевой ссылке, без входа в систему.

Решение оператора 2026-08-02, осознанный размен. Прежде здесь стояло правило
«деньги требуют опознанного человека», но опознавать оказалось некого: привязок
родитель↔ученик в системе ноль, почт родителей тоже ноль, а гостевыми ссылками
пользуются. Значит либо оплата живёт здесь, либо родитель не платит вовсе.

Чем это компенсировано:

* **Ученик берётся из ссылки, а не из запроса.** Номер начисления сверяется с
  `link.student_id`; чужой месяц по этой ссылке не оплатить и не увидеть.
* **Ограничение частоты.** У гостевого контура его не было вовсе; здесь оно
  обязательно — иначе вечная ссылка становится бесплатным каналом к дорогим
  запросам и к загрузке файлов на диск.
* **Журнал.** Каждое денежное действие по ссылке пишется в лог вместе с её
  номером: при споре «кто заплатил» иначе не осталось бы ничего.

Чего здесь сознательно НЕТ: просмотра чужих чеков и истории платежей сверх
сумм — гостю хватает того, что нужно заплатить.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.me_payments import store_receipt_payment
from app.core.config import Settings
from app.db.session import get_async_db
from app.models.parent_access_link import ParentAccessLink
from app.schemas.payment import (
    GatewayPaymentStart,
    PaymentStartRequest,
    StudentChargeRead,
)
from app.services import parent_access_link_service, payment_service, yookassa_service
from app.services.rate_limit_service import get_redis, is_rate_limited

logger = logging.getLogger(__name__)
settings = Settings()

router = APIRouter(prefix="/public/parent-dashboard", tags=["public_parent_payments"])

#: Чтения по ссылке: экран открывают руками, десятка в минуту хватает с запасом.
_READ_LIMIT = 30
#: Денежные действия: живой человек платит раз в месяц, не чаще.
_WRITE_LIMIT = 10
_WINDOW_SEC = 300


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _guard(request: Request, token: str, *, limit: int) -> None:
    """Ограничить частоту обращений к ссылке.

    Ключ — по ссылке И по адресу: один ключ по токену дал бы одному шумному
    клиенту возможность закрыть страницу всей семье, а один только по адресу не
    защитил бы от перебора токенов с разных адресов.
    """
    redis = get_redis(settings.redis_url)
    for key in (f"parent_link:{token[:16]}", f"parent_link_ip:{_client_ip(request)}"):
        if await is_rate_limited(redis, key, max_requests=limit, window_seconds=_WINDOW_SEC):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Слишком много запросов, попробуйте через несколько минут",
            )


async def _resolve_link(db: AsyncSession, token: str) -> ParentAccessLink:
    """Ссылка или 404. Отозванная неотличима от несуществующей."""
    link = await parent_access_link_service.resolve_token(db, token)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ссылка недействительна")
    return link


async def _own_charge(db: AsyncSession, link: ParentAccessLink, charge_id: int) -> dict:
    """Начисление, которое действительно принадлежит ученику из ссылки."""
    charge = await payment_service.charge_for_student(
        db, charge_id=charge_id, student_id=link.student_id
    )
    if charge is None:
        # Тот же ответ, что и на несуществующее начисление: иначе перебором
        # номеров стало бы видно, какие месяцы есть у других учеников.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Начисление не найдено")
    return charge


@router.get(
    "/{token}/charges",
    response_model=list[StudentChargeRead],
    summary="Начисления ученика по гостевой ссылке",
)
async def charges_by_link(
    request: Request,
    token: str = Path(..., min_length=8, max_length=128),
    db: AsyncSession = Depends(get_async_db),
) -> list[StudentChargeRead]:
    await _guard(request, token, limit=_READ_LIMIT)
    link = await _resolve_link(db, token)
    rows = await payment_service.list_student_charges(db, student_id=link.student_id)
    return [StudentChargeRead(**r) for r in rows]


@router.get(
    "/{token}/gateway-status",
    summary="Доступна ли оплата картой",
)
async def gateway_status_by_link(
    request: Request,
    token: str = Path(..., min_length=8, max_length=128),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    await _guard(request, token, limit=_READ_LIMIT)
    await _resolve_link(db, token)
    return {
        "enabled": yookassa_service.is_enabled(),
        "test_mode": yookassa_service.is_test_mode(),
        # Реквизиты перевода — основного способа оплаты.
        "transfer_details": settings.payment_transfer_details or None,
    }


@router.post(
    "/{token}/payments",
    status_code=status.HTTP_201_CREATED,
    summary="Приложить чек по гостевой ссылке",
)
async def submit_receipt_by_link(
    request: Request,
    token: str = Path(..., min_length=8, max_length=128),
    charge_id: int = Form(...),
    amount_minor: int = Form(..., gt=0),
    paid_on: Optional[date] = Form(default=None),
    payer_note: Optional[str] = Form(default=None, max_length=500),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    await _guard(request, token, limit=_WRITE_LIMIT)
    link = await _resolve_link(db, token)
    charge = await _own_charge(db, link, charge_id)

    if paid_on is not None and paid_on > date.today():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Дата платежа не может быть в будущем"
        )

    result = await store_receipt_payment(
        db,
        charge=charge,
        amount_minor=amount_minor,
        paid_on=paid_on,
        payer_note=payer_note,
        file=file,
        # Учётной записи у гостя нет: платёж останется без автора, и это честнее
        # приписки к ученику — чек прислал родитель, а не он.
        submitted_by=None,
    )
    logger.info(
        "tsk-010: чек по гостевой ссылке %s — ученик %s, начисление %s, платёж %s",
        link.id,
        link.student_id,
        charge_id,
        result.get("id"),
    )
    return result


@router.post(
    "/{token}/payments/gateway",
    response_model=GatewayPaymentStart,
    summary="Оплатить картой по гостевой ссылке",
)
async def start_gateway_by_link(
    request: Request,
    body: PaymentStartRequest,
    token: str = Path(..., min_length=8, max_length=128),
    db: AsyncSession = Depends(get_async_db),
) -> GatewayPaymentStart:
    await _guard(request, token, limit=_WRITE_LIMIT)
    link = await _resolve_link(db, token)
    charge = await _own_charge(db, link, body.charge_id)

    if not yookassa_service.is_enabled():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Оплата картой сейчас недоступна — приложите чек об оплате переводом",
        )

    rows = await payment_service.list_student_charges(db, student_id=link.student_id)
    found = next((r for r in rows if r["id"] == charge["id"]), None)
    remaining = int(found["due_minor"]) if found else 0
    amount = body.amount_minor or remaining
    if amount <= 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "По этому месяцу платить нечего — он уже оплачен"
        )
    if amount > remaining > 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Сумма больше остатка по месяцу"
        )

    try:
        payment = await yookassa_service.create_payment(
            amount_minor=amount,
            description=f"Обучение, {charge['period']:%m.%Y}",
            # Возврат — на ту же гостевую страницу, иначе родитель после оплаты
            # попадёт на форму входа, которой у него нет.
            return_url=f"{settings.public_base_url}/p/{token}",
            metadata={
                "charge_id": str(charge["id"]),
                "student_id": str(link.student_id),
                "parent_link_id": str(link.id),
            },
        )
    except yookassa_service.GatewayDisabledError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except yookassa_service.GatewayError as exc:
        logger.warning("tsk-010: шлюз не ответил на оплату по ссылке %s: %s", link.id, exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Платёжный сервис не ответил, попробуйте позже или приложите чек",
        ) from exc

    if payment.confirmation_url is None:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Платёжный сервис не вернул ссылку на оплату"
        )
    logger.info(
        "tsk-010: оплата картой по гостевой ссылке %s — ученик %s, начисление %s, платёж %s",
        link.id,
        link.student_id,
        body.charge_id,
        payment.id,
    )
    return GatewayPaymentStart(
        payment_id=payment.id,
        confirmation_url=payment.confirmation_url,
        amount_minor=payment.amount_minor,
        test_mode=payment.test,
    )
