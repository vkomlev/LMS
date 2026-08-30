"""tsk-010 — мягкая блокировка доступа к учёбе за неоплату.

Мягкая — значит закрыты материалы и задания, но кабинет, расписание и страница
оплаты остаются открытыми: иначе человек не увидит, сколько должен, и не сможет
заплатить. Дорога к оплате не перекрывается никогда.

Состояние НЕ хранится флагом и не ставится по расписанию: блокировка выводится
из тех же данных, что и сумма долга. Поэтому она наступает ровно в свой день и
снимается сама в момент оплаты — без крона, который мог бы не сработать, и без
поля, которое могло бы разъехаться с деньгами.

Момент блокировки НЕ совпадает с пометкой «просрочено». Месяц оплачивается до
своего конца (за август — до 31 августа), пометка и письмо появляются с 1-го
числа следующего, а занятия закрываются лишь через несколько дней после этого:
человеку нужно время заплатить, когда месяц уже кончился.

Почему не через `user_courses.is_active`: по этому полю строится расчёт
начислений. Сняв его, мы стёрли бы сам долг — человек стал бы «не должен»
ровно потому, что не заплатил.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services import charge_service, payment_block_hold_service, payment_service
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)
settings = Settings()

__all__ = [
    "BlockingDebt",
    "blocking_debt",
    "has_blocking_debt",
    "assert_content_allowed",
    "blocked_message",
    "payments_url",
    "BLOCKED_MESSAGE",
    "PAYMENT_OVERDUE_CODE",
]

BLOCKED_MESSAGE = (
    "Занятия приостановлены из-за неоплаты. Оплатите в разделе «Оплата» — "
    "доступ откроется сразу после подтверждения."
)

#: Машинный признак отказа в теле ответа (tsk-617). Клиент отличает «не
#: заплатил» от «сломалось» по нему, а не по словам: формулировка продуктовая и
#: меняется вместе с ценами, а разбор по тексту сломался бы молча — ученик снова
#: увидел бы «сбой на нашей стороне». Тот же приём, что у `subscription_denied`.
PAYMENT_OVERDUE_CODE = "payment_overdue"


@dataclass(frozen=True)
class BlockingDebt:
    """Долг, который уже закрывает занятия: сколько и за какие месяцы.

    Сумма и месяцы едут в теле отказа: «оплатите» без числа и без месяца
    заставляет человека идти искать, что именно он должен, — а идти ему некуда,
    учебный контент как раз закрыт.
    """

    due_minor: int
    periods: tuple[date, ...]


def payments_url() -> str:
    """Адрес кабинета оплаты — тот же, что в письме о просрочке.

    Клиенту без него некуда вести: в Telegram-боте раздела «Оплата» нет, и
    ссылка — единственная дорога к деньгам из отказа.
    """
    return f"{settings.public_base_url.rstrip('/')}/me/payments"


def _format_amount(due_minor: int) -> str:
    """Сумма человеку: копейки показываем, только если они есть."""
    if due_minor % 100 == 0:
        return f"{due_minor // 100} ₽"
    return f"{due_minor / 100:.2f} ₽"


def blocked_message(debt: BlockingDebt) -> str:
    """Текст отказа с суммой и месяцами.

    Формат месяца `%m.%Y` — тот же, что в письме о просрочке
    (`notification_email_service`): человек получает письмо и видит отказ, и
    разные формы одной и той же даты выглядели бы как разные долги.
    """
    if not debt.periods:
        return BLOCKED_MESSAGE
    months = ", ".join(f"{p:%m.%Y}" for p in debt.periods)
    return (
        f"Занятия приостановлены из-за неоплаты: {months} — "
        f"{_format_amount(debt.due_minor)}. Оплатите в разделе «Оплата» — "
        "доступ откроется сразу после подтверждения."
    )


async def blocking_debt(
    db: AsyncSession, student_id: int, *, today: Optional[date] = None
) -> Optional[BlockingDebt]:
    """Долг ученика, который уже закрывает занятия, — или None.

    Приложенный чек, ждущий подтверждения, долгом не считается: человек своё
    сделал, дальше очередь наша — закрывать ему занятия за это нельзя.

    Действующая отсрочка (tsk-744) закрывает вопрос ещё раньше: оператор
    договорился с семьёй подождать, и до названного дня занятия не трогаем. Долг
    при этом никуда не девается — он виден на экране оплаты и держит плашку в
    кабинете; отложен только момент, когда закрывается учебный контент.
    """
    today = today or date.today()
    hold = await payment_block_hold_service.active_hold(db, student_id, today=today)
    if hold is not None:
        logger.info(
            "tsk-744: блокировка ученику %s не применяется — отсрочка до %s",
            student_id,
            hold.until,
        )
        return None
    rows = (
        await db.execute(
            text(
                """
                SELECT ch.period,
                       ch.calculated_minor,
                       ch.manual_minor,
                       COALESCE(adj.total, 0)  AS adjustments_minor,
                       COALESCE(pay.paid, 0)    AS paid_minor,
                       COALESCE(pay.pending, 0) AS pending_minor
                  FROM student_monthly_charge ch
                  LEFT JOIN LATERAL (
                        SELECT sum(a.amount_minor) AS total
                          FROM charge_adjustment a
                         WHERE a.student_id = ch.student_id
                           AND a.group_id = ch.group_id
                           AND a.period = ch.period
                  ) adj ON TRUE
                  LEFT JOIN LATERAL (
                        SELECT sum(p.amount_minor) FILTER (WHERE p.status = 'confirmed') AS paid,
                               sum(p.amount_minor) FILTER (WHERE p.status = 'pending')   AS pending
                          FROM student_payment p
                         WHERE p.student_id = ch.student_id
                           AND p.group_id = ch.group_id
                           AND p.period = ch.period
                  ) pay ON TRUE
                 WHERE ch.student_id = :s
                   AND ch.status = 'open'
                """
            ),
            {"s": student_id},
        )
    ).all()

    due_minor = 0
    periods: list[date] = []
    for row in rows:
        total_minor = charge_service.charge_total_minor(
            calculated_minor=row.calculated_minor,
            manual_minor=row.manual_minor,
            adjustments_minor=int(row.adjustments_minor),
        )
        state = payment_service.payment_state(
            total_minor=total_minor,
            paid_minor=int(row.paid_minor),
            pending_minor=int(row.pending_minor),
            period=row.period,
            today=today,
        )
        if state.is_blocked:
            due_minor += state.due_minor
            periods.append(row.period)

    if not periods:
        return None
    return BlockingDebt(due_minor=due_minor, periods=tuple(sorted(periods)))


async def has_blocking_debt(
    db: AsyncSession, student_id: int, *, today: Optional[date] = None
) -> bool:
    """Есть ли у ученика долг, который уже закрывает занятия."""
    return await blocking_debt(db, student_id, today=today) is not None


async def assert_content_allowed(db: AsyncSession, student_id: int) -> None:
    """Закрыть учебный контент, если оплата просрочена.

    Вызывается ПОСЛЕ проверок роли и владения: у преподавателя и методиста своя
    причина открывать материалы, и долг ученика её не отменяет.

    Проверяется УЧЕНИК, а не вызывающий (tsk-617): бот ходит по сервисному
    ключу, и освобождение сервисного вызова означало бы «через бота можно,
    через браузер нельзя» — блокировка обходилась бы сменой клиента. Там, где
    ученика назвать нечем (чтение материала по своей сессии), проверка остаётся
    по `current_user`.

    Отказ — `DomainError`, а не голый `HTTPException`: в теле едут машинный
    признак, сумма, месяцы и ссылка на кабинет оплаты. Без них клиенту нечего
    показать, кроме «недостаточно прав», — а это отправляет человека ждать
    вместо того, чтобы заплатить.
    """
    debt = await blocking_debt(db, student_id)
    if debt is None:
        return
    logger.info(
        "tsk-010: доступ к учебному контенту закрыт ученику %s — просрочена оплата "
        "(долг %s коп., месяцы %s)",
        student_id,
        debt.due_minor,
        ", ".join(f"{p:%m.%Y}" for p in debt.periods),
    )
    raise DomainError(
        detail=blocked_message(debt),
        status_code=403,
        payload={
            "code": PAYMENT_OVERDUE_CODE,
            "due_minor": debt.due_minor,
            "periods": [p.isoformat() for p in debt.periods],
            "payments_url": payments_url(),
        },
    )
