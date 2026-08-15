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
from app.services import (
    entitlements_service,
    payment_service,
    subscription_service,
    yookassa_service,
)

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


#: Назначение платежа в `metadata`. Без него уведомление о пакете было бы
#: неотличимо от уведомления об оплате месяца — а зачисляются они по-разному.
#: Метка одна на весь путь денег: тем же значением помечается строка платежа
#: (`student_payment.purpose`), по нему же сверка узнаёт разовую покупку.
_PURPOSE_AI_PACKAGE = payment_service.PURPOSE_AI_PACKAGE


@router.post(
    "/payments/yookassa/ai-package",
    response_model=GatewayPaymentStart,
    summary="Купить пакет обращений к ИИ-наставнику",
    responses={
        403: {"description": "Пакет не имеет смысла на текущем тарифе"},
        503: {"description": "Оплата картой выключена"},
    },
)
async def start_ai_package_payment(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> GatewayPaymentStart:
    """Завести платёж за пакет. Зачисление — по уведомлению, а не здесь.

    Пакет продаётся только тому, кому он что-то даёт: на тарифе со счётным
    лимитом. На `demo`/`alumni` наставника нет вовсе, на `test`/`flagship` он
    безлимитный — в обоих случаях деньги взять было бы нечестно.
    """
    if current_user.is_service:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Оплата принимается только от пользователя, не от сервисного ключа",
        )
    if not yookassa_service.is_enabled():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Оплата картой сейчас недоступна"
        )

    decision = await entitlements_service.check(
        db, student_id=current_user.id, capability="ai_tutor"
    )
    if decision.limit is None:
        # Признак — наличие ЧИСЛЕННОГО лимита. По `allowed` эти случаи не
        # различить: при исчерпанном лимите он тоже False, а пакет там как раз
        # и нужен.
        #
        # `should_block` здесь намеренно не зовётся: это правило продажи, а не
        # гейт доступа. Оно не должно зависеть от режима выката — продавать
        # пакет безлимитному тарифу нечестно и при выключенном гейте.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            decision.upgrade_hint
            or "На вашем тарифе пакет обращений к наставнику не нужен",
        )

    units = settings.ai_package_units
    try:
        payment = await yookassa_service.create_payment(
            amount_minor=settings.ai_package_price_minor,
            description=f"Пакет обращений к наставнику, {units} шт.",
            return_url=f"{settings.public_base_url}/me/subscription",
            metadata={
                "purpose": _PURPOSE_AI_PACKAGE,
                "student_id": str(current_user.id),
                "units": str(units),
            },
        )
    except yookassa_service.GatewayDisabledError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except yookassa_service.GatewayError as exc:
        logger.warning("tsk-301: не удалось завести платёж за пакет: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Платёжный сервис не ответил, попробуйте позже"
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


_PURPOSE_SUBSCRIPTION = "subscription"


@router.get(
    "/payments/plans",
    summary="Тарифы, которые можно купить самому",
)
async def purchasable_plans(
    db: AsyncSession = Depends(get_async_db),
) -> list[dict]:
    """Витрина самостоятельной покупки.

    Открыта без авторизации намеренно: человек с улицы выбирает тариф ДО того,
    как заведёт учётную запись. Секретов здесь нет — те же цены на сайте.
    """
    return await subscription_service.purchasable_plans(db)


@router.post(
    "/payments/yookassa/subscription",
    response_model=GatewayPaymentStart,
    summary="Купить или сменить тариф самостоятельно",
    responses={
        403: {"description": "Этот тариф самому не купить"},
        503: {"description": "Оплата картой выключена"},
    },
)
async def start_subscription_payment(
    plan_code: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> GatewayPaymentStart:
    """Завести платёж за тариф. Права выдаются по уведомлению, а не здесь.

    Самому продаются только тарифы без занятий: расписание заводит методист, и
    продавать через кнопку то, что некому выполнить, нельзя.
    """
    if current_user.is_service:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Оплата принимается только от пользователя, не от сервисного ключа",
        )
    if not yookassa_service.is_enabled():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Оплата картой сейчас недоступна"
        )

    plans = {p["code"]: p for p in await subscription_service.purchasable_plans(db)}
    plan = plans.get(plan_code)
    if plan is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Этот тариф нельзя купить самостоятельно — напишите преподавателю",
        )

    try:
        payment = await yookassa_service.create_payment(
            amount_minor=int(plan["price_minor"]),
            description=f"Тариф «{plan['name']}», месяц",
            return_url=f"{settings.public_base_url}/me/subscription",
            metadata={
                "purpose": _PURPOSE_SUBSCRIPTION,
                "student_id": str(current_user.id),
                "plan_code": plan_code,
            },
        )
    except yookassa_service.GatewayDisabledError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except yookassa_service.GatewayError as exc:
        logger.warning("tsk-301: не удалось завести платёж за тариф: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Платёжный сервис не ответил, попробуйте позже"
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
    if payment.metadata.get("purpose") == _PURPOSE_AI_PACKAGE:
        return await _record_ai_package(db, payment)
    if payment.metadata.get("purpose") == _PURPOSE_SUBSCRIPTION:
        return await _record_subscription(db, payment)

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


async def _record_ai_package(db: AsyncSession, payment) -> dict:
    """Зачислить оплаченный пакет обращений (пробел П6 контракта).

    **«Деньги списаны, грант не создан» — главный риск этого пути.** Поэтому
    любой сбой зачисления отвечает 5xx, а не 200: платёжный сервис повторит
    доставку, и пакет доедет сам. Проглотить ошибку молча значило бы взять
    деньги и не дать за них ничего — а узнать об этом можно было бы только по
    жалобе человека.

    Повторная доставка того же платежа безопасна: уникальный
    `gateway_payment_id` не даст зачислить пакет дважды.

    tsk-615: вместе с пакетом здесь же учитываются деньги за него. Раньше
    записывался только пакет, и выручка от покупки не попадала ни в кабинет, ни
    в выгрузку для сверки со шлюзом.
    """
    student_id = _as_int(payment.metadata.get("student_id"))
    units = _as_int(payment.metadata.get("units"))
    if student_id is None or units is None or units <= 0:
        # Чинить нечем: в ответе шлюза нет, кому и сколько зачислять. Повторять
        # доставку бессмысленно — отвечаем 200, но громко, чтобы это нашли.
        logger.error(
            "tsk-301: платёж %s за пакет без ученика или объёма (metadata=%s)",
            payment.id, payment.metadata,
        )
        return {"status": "ignored"}

    try:
        granted, recorded = await subscription_service.record_ai_package_purchase(
            db, student_id, units=units, gateway_payment_id=payment.id,
            amount_minor=payment.amount_minor,
        )
    except Exception as exc:
        await db.rollback()
        logger.error(
            "tsk-301: ДЕНЬГИ СПИСАНЫ, ПАКЕТ НЕ ЗАЧИСЛЕН — платёж %s, ученик %s, "
            "%s обращений: %s",
            payment.id, student_id, units, exc, exc_info=True,
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Не удалось зачислить пакет, повторите доставку уведомления",
        ) from exc

    return {"status": "recorded" if (granted or recorded) else "already_recorded"}


async def _record_subscription(db: AsyncSession, payment) -> dict:
    """Выдать оплаченный тариф (пробел П6 контракта, вторая его половина).

    Тот же принцип, что и у пакета: сбой отвечает 5xx, чтобы платёжный сервис
    повторил доставку. Здесь цена ошибки выше — человек остался бы без доступа,
    за который заплатил, и первым об этом узнал бы он сам.
    """
    student_id = _as_int(payment.metadata.get("student_id"))
    plan_code = (payment.metadata.get("plan_code") or "").strip()
    if student_id is None or not plan_code:
        logger.error(
            "tsk-301: платёж %s за тариф без ученика или кода тарифа (metadata=%s)",
            payment.id, payment.metadata,
        )
        return {"status": "ignored"}

    try:
        created = await subscription_service.purchase_plan(
            db, student_id, plan_code,
            gateway_payment_id=payment.id,
            amount_minor=payment.amount_minor,
        )
    except ValueError as exc:
        # Тариф исчез или стал непродаваемым между заведением платежа и
        # уведомлением. Повторять бессмысленно — нужен человек.
        logger.error(
            "tsk-301: ДЕНЬГИ СПИСАНЫ, ТАРИФ НЕ ВЫДАН — платёж %s, ученик %s, "
            "тариф %s: %s", payment.id, student_id, plan_code, exc,
        )
        return {"status": "ignored"}
    except Exception as exc:
        await db.rollback()
        logger.error(
            "tsk-301: ДЕНЬГИ СПИСАНЫ, ТАРИФ НЕ ВЫДАН — платёж %s, ученик %s, "
            "тариф %s: %s", payment.id, student_id, plan_code, exc, exc_info=True,
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Не удалось выдать тариф, повторите доставку уведомления",
        ) from exc

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
