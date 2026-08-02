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
from datetime import date
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import charge_service, payment_service

logger = logging.getLogger(__name__)

__all__ = ["has_blocking_debt", "assert_content_allowed", "BLOCKED_MESSAGE"]

BLOCKED_MESSAGE = (
    "Занятия приостановлены из-за неоплаты. Оплатите в разделе «Оплата» — "
    "доступ откроется сразу после подтверждения."
)


async def has_blocking_debt(
    db: AsyncSession, student_id: int, *, today: Optional[date] = None
) -> bool:
    """Есть ли у ученика долг, который уже закрывает занятия.

    Приложенный чек, ждущий подтверждения, долгом не считается: человек своё
    сделал, дальше очередь наша — закрывать ему занятия за это нельзя.
    """
    today = today or date.today()
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
            return True
    return False


async def assert_content_allowed(db: AsyncSession, student_id: int) -> None:
    """Закрыть учебный контент, если оплата просрочена.

    Вызывается ПОСЛЕ проверок роли и владения: у преподавателя, методиста и
    сервисного ключа своя причина открывать материалы, и долг ученика её не
    отменяет.
    """
    if await has_blocking_debt(db, student_id):
        logger.info(
            "tsk-010: доступ к учебному контенту закрыт ученику %s — просрочена оплата",
            student_id,
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, BLOCKED_MESSAGE)
