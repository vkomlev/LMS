"""tsk-010 — очередь подтверждения оплат в кабинете маркетолога."""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, require_role
from app.api.v1.me_payments import receipt_response
from app.core.config import Settings
from app.db.session import get_async_db
from app.schemas.payment import (
    PaymentDecisionRequest,
    PaymentExportRow,
    PaymentPurpose,
    PaymentRead,
    PaymentStatus,
)
from app.services import (
    payment_reminder_service,
    payment_service,
    subscription_service,
    yookassa_service,
)

#: Метка разовой покупки пакета — та же, что кладётся в metadata при заведении
#: платежа (`payments_gateway`) и в строку платежа.
_PURPOSE_AI_PACKAGE = payment_service.PURPOSE_AI_PACKAGE

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
    description=(
        "Без фильтра сверху идут платежи, ждущие решения. Разовые покупки "
        "(пакет обращений к наставнику) идут в том же списке: у них пусты месяц "
        "и тарифная группа, а назначение видно в поле purpose."
    ),
)
async def list_payments(
    payment_status: Optional[PaymentStatus] = Query(default=None, alias="status"),
    period: Optional[date] = Query(default=None),
    student_id: Optional[int] = Query(default=None),
    purpose: Optional[PaymentPurpose] = Query(
        default=None, description="Оставить только платежи этого назначения"
    ),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_payments_gate),
) -> list[PaymentRead]:
    rows = await payment_service.list_payments(
        db,
        status=payment_status,
        period=period,
        student_id=student_id,
        purpose=purpose,
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
) -> StreamingResponse:
    payment = await payment_service.get_receipt(db, payment_id=payment_id)
    if payment is None or payment["receipt_file"] is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Чек не найден")
    # Общая выдача файла с типом по имени В ХРАНИЛИЩЕ — см. `me_payments`:
    # присланное имя доверять нельзя, иначе `evil.svg` вернётся активным.
    return await receipt_response(payment)


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


@router.get(
    "/payments/reminders",
    summary="Кому уйдёт напоминание о просрочке",
    description=(
        "Предпросмотр перед отправкой: кто просрочил, кому уже писали на этой "
        "неделе и кому написать некуда — у того нет почты."
    ),
)
async def reminders_preview(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_payments_gate),
) -> dict:
    debtors = await payment_reminder_service.list_overdue(db)
    return {
        "total": len(debtors),
        "will_send": [
            _debtor_view(d) for d in debtors if d.email and not d.reminded_recently
        ],
        "already_reminded": [
            _debtor_view(d) for d in debtors if d.email and d.reminded_recently
        ],
        # Этим письмо не уйдёт — с ними нужно связаться самому.
        "without_email": [_debtor_view(d) for d in debtors if not d.email],
    }


@router.post(
    "/payments/reminders/send",
    summary="Отправить напоминания о просрочке",
    description=(
        "Письма уходят только тем, кому на этой неделе ещё не писали. "
        "Ученикам без почты не уходит ничего — они возвращаются списком."
    ),
)
async def reminders_send(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_payments_gate),
) -> dict:
    run = await payment_reminder_service.send_reminders(db, sent_by=current_user.id)
    return {
        "sent": run.sent,
        "failed": run.failed,
        "skipped_recent": run.skipped_recent,
        "without_email": run.without_email,
    }


def _debtor_view(debtor: payment_reminder_service.OverdueDebtor) -> dict:
    return {
        "student_id": debtor.student_id,
        "full_name": debtor.full_name,
        "period": debtor.period.isoformat(),
        "group_name": debtor.group_name,
        "due_minor": debtor.due_minor,
        # Сам адрес не отдаём: на экране он не нужен, а в логах браузера лишний.
        "has_email": debtor.email is not None,
    }


@router.post(
    "/payments/reconcile",
    summary="Сверить оплаты картой со шлюзом",
    description=(
        "Берёт успешные платежи платёжного сервиса за период и дозакрывает те, "
        "что у нас не учтены. Нужна, когда уведомление не дошло: деньги списаны, "
        "а в кабинете висит долг."
    ),
)
async def reconcile(
    date_from: date = Query(...),
    date_to: date = Query(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_payments_gate),
) -> dict:
    if date_to < date_from:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Конец периода раньше начала"
        )
    try:
        payments = await yookassa_service.list_succeeded(
            created_from=date_from, created_to=date_to
        )
    except yookassa_service.GatewayDisabledError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except yookassa_service.GatewayError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Платёжный сервис не ответил"
        ) from exc

    added = 0
    skipped_no_charge: list[str] = []
    for payment in payments:
        if not payment.paid:
            continue
        # tsk-615: разовая покупка попадала сюда как «платёж без начисления» —
        # то есть сверка сама объявляла деньги необъяснимыми. Начисления у неё
        # нет и не будет, зато есть назначение: учитываем по нему.
        if payment.metadata.get("purpose") == _PURPOSE_AI_PACKAGE:
            outcome = await _reconcile_ai_package(db, payment)
            if outcome is None:
                # Учесть нечем — нужен человек. Отличается от «уже учтено»:
                # иначе каждая повторная сверка объявляла бы давно закрытую
                # покупку проблемной, и список проблем перестали бы читать.
                skipped_no_charge.append(payment.id)
            elif outcome:
                added += 1
            continue
        charge_id = payment.metadata.get("charge_id")
        charge = (
            await payment_service.charge_by_id(db, charge_id=int(charge_id))
            if str(charge_id or "").isdigit()
            else None
        )
        if charge is None:
            # Платёж есть, а начисления нет — молча пропускать нельзя: это
            # деньги, которые некуда положить, и человек должен о них узнать.
            skipped_no_charge.append(payment.id)
            continue
        if await payment_service.record_gateway_payment(
            db,
            student_id=charge["student_id"],
            group_id=charge["group_id"],
            period=charge["period"],
            amount_minor=payment.amount_minor,
            gateway="yookassa",
            gateway_payment_id=payment.id,
            paid_on=date_from if date_from == date_to else None,
        ):
            added += 1

    logger.info(
        "tsk-010: сверка за %s—%s: у шлюза %s, дозакрыто %s, без начисления %s",
        date_from,
        date_to,
        len(payments),
        added,
        len(skipped_no_charge),
    )
    return {
        "checked": len(payments),
        "added": added,
        "already_recorded": len(payments) - added - len(skipped_no_charge),
        "without_charge": skipped_no_charge,
    }


async def _reconcile_ai_package(db: AsyncSession, payment) -> Optional[bool]:
    """Дозакрыть разовую покупку пакета, если уведомление не дошло (tsk-615).

    Тот же путь, что и у приёма уведомления, — иначе сверка чинила бы деньги, не
    выдавая пакет, и человек остался бы с оплаченной, но невидимой покупкой.

    Три исхода, и их нельзя сводить к двум:
      * `True` — деньги учтены сейчас;
      * `False` — они были учтены раньше (обычный случай повторной сверки);
      * `None` — учесть нечем: в ответе шлюза нет ученика или объёма пакета.

    Только третий исход попадает в список проблемных. Если бы «уже учтено»
    считалось проблемой, каждая следующая сверка показывала бы давно закрытую
    покупку как расхождение — и список перестали бы читать.
    """
    student_id = _as_int(payment.metadata.get("student_id"))
    units = _as_int(payment.metadata.get("units"))
    if student_id is None or units is None or units <= 0:
        logger.error(
            "tsk-615: сверка нашла платёж %s за пакет без ученика или объёма "
            "(metadata=%s) — учесть нечем",
            payment.id, payment.metadata,
        )
        return None

    _, recorded = await subscription_service.record_ai_package_purchase(
        db, student_id, units=units, gateway_payment_id=payment.id,
        amount_minor=payment.amount_minor,
    )
    return recorded


def _as_int(value: object) -> Optional[int]:
    """Число из метаданных шлюза. Чужой ввод — поэтому через попытку."""
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


async def _reload(db: AsyncSession, payment_id: int) -> PaymentRead:
    """Перечитать платёж после решения — отдаём то, что реально в базе.

    Фильтр по номеру идёт в SQL, а не постфильтром по всему списку платежей:
    иначе каждое решение вычитывало бы таблицу целиком.
    """
    rows = await payment_service.list_payments(db, payment_id=payment_id)
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Платёж не найден")
    return PaymentRead(**rows[0])
