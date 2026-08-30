"""tsk-744 — отсрочка блокировки за неоплату конкретному ученику.

Блокировка выводится из данных и наступает сама (`payment_access_service`). Это
её достоинство, и одновременно причина, по которой оператору нечем было пойти
навстречу одной семье: сдвинуть срок можно было только всей школе сразу.

Отсрочка ничего не прощает и долг не гасит: месяц по-прежнему не оплачен, сумма
на экране оплаты та же, плашка в кабинете висит. Отложен только момент, когда
закрывается учебный контент. Иначе «пойти навстречу» означало бы стереть долг —
ровно та ошибка, из-за которой блокировку нельзя делать через
`user_courses.is_active`.

Бессрочной отсрочки нет (решение оператора 31.08): срок обязателен и истекает
сам, поэтому про ученика невозможно забыть. Нужно дольше — ставится новая
отсрочка, и в истории видно, сколько раз откладывали.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

__all__ = [
    "BlockHold",
    "MAX_HOLD_DAYS",
    "active_hold",
    "list_holds",
    "create_hold",
    "cancel_hold",
]

#: Насколько далеко можно отложить блокировку одним решением. Не «на всякий
#: случай»: без верхней границы отсрочка «до 2099 года» становится той самой
#: бессрочной, которую оператор отверг, — только выглядит как срочная.
MAX_HOLD_DAYS = 90


@dataclass(frozen=True)
class BlockHold:
    """Одна отсрочка: до когда, почему, кто поставил и снята ли."""

    id: int
    student_id: int
    student_name: Optional[str]
    until: date
    reason: str
    created_by: Optional[int]
    created_by_name: Optional[str]
    created_at: datetime
    cancelled_at: Optional[datetime]
    #: Действует прямо сейчас. Считается на сервере, чтобы экран и гейт доступа
    #: не разошлись в трактовке «сегодня».
    is_active: bool


_SELECT = """
    SELECT h.id, h.student_id, u.full_name AS student_name, h.until, h.reason,
           h.created_by, a.full_name AS created_by_name,
           h.created_at, h.cancelled_at,
           (h.cancelled_at IS NULL AND h.until >= :today) AS is_active
      FROM payment_block_hold h
      JOIN users u ON u.id = h.student_id
      LEFT JOIN users a ON a.id = h.created_by
"""


def _row_to_hold(row) -> BlockHold:
    """Строка запроса → объект. Собран в одном месте: полей девять."""
    return BlockHold(
        id=row.id,
        student_id=row.student_id,
        student_name=row.student_name,
        until=row.until,
        reason=row.reason,
        created_by=row.created_by,
        created_by_name=row.created_by_name,
        created_at=row.created_at,
        cancelled_at=row.cancelled_at,
        is_active=bool(row.is_active),
    )


async def active_hold(
    db: AsyncSession, student_id: int, *, today: Optional[date] = None
) -> Optional[BlockHold]:
    """Действующая отсрочка ученика — или None.

    Если их несколько (оператор поставил новую, не сняв старую), берётся самая
    дальняя: человеку идут навстречу, а не отсчитывают по строгой.
    """
    today = today or date.today()
    row = (
        await db.execute(
            text(
                _SELECT
                + """
                 WHERE h.student_id = :s
                   AND h.cancelled_at IS NULL
                   AND h.until >= :today
                 ORDER BY h.until DESC
                 LIMIT 1
                """
            ),
            {"s": student_id, "today": today},
        )
    ).first()
    return _row_to_hold(row) if row is not None else None


async def list_holds(
    db: AsyncSession,
    *,
    student_id: Optional[int] = None,
    only_active: bool = False,
    today: Optional[date] = None,
) -> list[BlockHold]:
    """Отсрочки: по ученику или все сразу, с историей или только действующие."""
    today = today or date.today()
    rows = (
        await db.execute(
            text(
                _SELECT
                + """
                 -- CAST на параметре: у необязательного фильтра asyncpg иначе
                 -- не выводит тип NULL и роняет запрос целиком.
                 WHERE (CAST(:s AS integer) IS NULL OR h.student_id = CAST(:s AS integer))
                   AND (NOT :only_active
                        OR (h.cancelled_at IS NULL AND h.until >= :today))
                 ORDER BY h.created_at DESC
                """
            ),
            {"s": student_id, "only_active": only_active, "today": today},
        )
    ).all()
    return [_row_to_hold(r) for r in rows]


async def create_hold(
    db: AsyncSession,
    *,
    student_id: int,
    until: date,
    reason: str,
    created_by: int,
    today: Optional[date] = None,
) -> BlockHold:
    """Отложить блокировку ученику до указанного дня включительно.

    Прошедшая дата и пустая причина отбиваются здесь, а не только формой: те же
    правила действуют для любого клиента, а форма — лишь один из них.

    Прежние действующие отсрочки этого ученика снимаются: иначе «сократить
    отсрочку» было бы невозможно — старая, более дальняя, продолжала бы
    действовать, и оператор считал бы, что укоротил срок, а он бы не изменился.
    """
    today = today or date.today()
    reason = (reason or "").strip()
    if not reason:
        raise DomainError(detail="Укажите причину отсрочки", status_code=422)
    if until < today:
        raise DomainError(
            detail="Отложить можно только на будущее: дата уже прошла",
            status_code=422,
        )
    limit = today + timedelta(days=MAX_HOLD_DAYS)
    if until > limit:
        raise DomainError(
            detail=(
                f"Отложить можно не больше чем на {MAX_HOLD_DAYS} дней — "
                f"до {limit:%d.%m.%Y}"
            ),
            status_code=422,
        )

    await db.execute(
        text(
            """
            UPDATE payment_block_hold
               SET cancelled_at = now(), cancelled_by = :by
             WHERE student_id = :s
               AND cancelled_at IS NULL
               AND until >= :today
            """
        ),
        {"s": student_id, "by": created_by, "today": today},
    )
    new_id = (
        await db.execute(
            text(
                """
                INSERT INTO payment_block_hold (student_id, until, reason, created_by)
                VALUES (:s, :until, :reason, :by)
                RETURNING id
                """
            ),
            {"s": student_id, "until": until, "reason": reason, "by": created_by},
        )
    ).scalar_one()
    await db.commit()

    logger.info(
        "tsk-744: блокировка ученику %s отложена до %s (кто: %s, причина: %s)",
        student_id,
        until,
        created_by,
        reason,
    )
    holds = await list_holds(db, student_id=student_id, today=today)
    return next(h for h in holds if h.id == new_id)


async def cancel_hold(
    db: AsyncSession, *, hold_id: int, cancelled_by: int
) -> Optional[BlockHold]:
    """Снять отсрочку досрочно. Строка остаётся — стирается только действие.

    Возвращает None, если такой отсрочки нет или она уже снята: повторное
    нажатие не должно выглядеть как новое событие.
    """
    row = (
        await db.execute(
            text(
                """
                UPDATE payment_block_hold
                   SET cancelled_at = now(), cancelled_by = :by
                 WHERE id = :id AND cancelled_at IS NULL
                 RETURNING id, student_id
                """
            ),
            {"id": hold_id, "by": cancelled_by},
        )
    ).first()
    if row is None:
        return None
    await db.commit()
    logger.info(
        "tsk-744: отсрочка %s ученику %s снята досрочно (кто: %s)",
        hold_id,
        row.student_id,
        cancelled_by,
    )
    holds = await list_holds(db, student_id=row.student_id)
    return next((h for h in holds if h.id == hold_id), None)
