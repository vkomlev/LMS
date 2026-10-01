"""tsk-1194 — исход «принят уход без оплаты» у долга ушедшего ученика.

Политика школы (оператор 01.10.2026): ушёл и не заплатил — значит, не заплатит;
такой долг только искажает картину. Он не входит в итоги начислений, долги,
напоминания, рассылки и блокировку, а на экране лежит отдельным свёрнутым
блоком. Строка и сумма остаются в истории — это отметка, а не удаление.

Отметка ставится сама при выпуске (`graduation_service.apply`), а для уже
ушедших — разовым проходом :func:`write_off_alumni_debts`. Снять её можно
кнопкой «Вернуть в долги». Оплата, пришедшая позже, гасит остаток, и строка
становится оплаченной без всякого снятия — исход описывает только неоплаченный
остаток (см. `payment_service.attach_payment_state`).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Iterable, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import payment_reminder_service

logger = logging.getLogger(__name__)

__all__ = [
    "write_off_lines",
    "write_off_charge",
    "restore_charge",
    "write_off_alumni_debts",
]


async def write_off_lines(
    db: AsyncSession,
    *,
    student_id: int,
    lines: Iterable[tuple[int, date]],
    written_off_by: Optional[int],
) -> int:
    """Отметить месяцы ученика (группа, период) как принятый уход без оплаты.

    Уже отмеченные не трогаются: первая отметка — момент решения, и её время
    не должно переезжать от повторного прохода. Не коммитит — вызывающий
    решает, частью какого целого это действие.
    """
    marked = 0
    for group_id, period in lines:
        res = await db.execute(
            text(
                "UPDATE student_monthly_charge "
                "   SET written_off_at = now(), written_off_by = :by, updated_at = now() "
                " WHERE student_id = :s AND group_id = :g AND period = :p "
                "   AND written_off_at IS NULL"
            ),
            {"s": student_id, "g": group_id, "p": period, "by": written_off_by},
        )
        marked += res.rowcount
    if marked:
        logger.info(
            "tsk-1194: ученику %s принят уход без оплаты по %s мес.", student_id, marked
        )
    return marked


async def write_off_charge(
    db: AsyncSession, *, charge_id: int, written_off_by: Optional[int]
) -> bool:
    """Отметить одну строку кнопкой. False — строки нет."""
    res = await db.execute(
        text(
            "UPDATE student_monthly_charge "
            "   SET written_off_at = COALESCE(written_off_at, now()), "
            "       written_off_by = COALESCE(written_off_by, :by), updated_at = now() "
            " WHERE id = :id"
        ),
        {"id": charge_id, "by": written_off_by},
    )
    await db.commit()
    return res.rowcount > 0


async def restore_charge(db: AsyncSession, *, charge_id: int) -> bool:
    """«Вернуть в долги»: снять исход, остаток снова считается долгом."""
    res = await db.execute(
        text(
            "UPDATE student_monthly_charge "
            "   SET written_off_at = NULL, written_off_by = NULL, updated_at = now() "
            " WHERE id = :id"
        ),
        {"id": charge_id},
    )
    await db.commit()
    return res.rowcount > 0


async def write_off_alumni_debts(
    db: AsyncSession, *, written_off_by: Optional[int] = None, apply: bool = False
) -> list[dict]:
    """Разовый проход по уже ушедшим: их долги — принятый уход без оплаты.

    Список берётся тем же правилом, что и блок «долг ушедших» (tsk-925), —
    своя копия признака «ушёл» разъехалась бы с ним. Без `apply` только
    показывает, кого затронет.
    """
    debts = await payment_reminder_service.list_alumni_debts(db)
    plan = [
        {
            "student_id": d.student_id,
            "full_name": d.full_name,
            "group_id": d.group_id,
            "period": d.period,
            "due_minor": d.due_minor,
        }
        for d in debts
    ]
    if apply:
        for row in plan:
            await write_off_lines(
                db,
                student_id=row["student_id"],
                lines=[(row["group_id"], row["period"])],
                written_off_by=written_off_by,
            )
        await db.commit()
    return plan
