"""tsk-513 — перерывы ученика: хранение, гашение занятий, возврат.

Перерыв гасит занятия статусом `on_break`, а не удаляет их. Это нужно, чтобы
отличать «не придёт, потому что перерыв» от «отказался» и «не пришёл»: последнее
— проступок, а первое — договорённость, и в отчётах они не должны сливаться.

Гасим только `scheduled`. Прошедшие отметки (`confirmed`, `no_show`, `completed`)
не трогаем никогда: это зафиксированные факты, а не планы.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

__all__ = [
    "list_breaks",
    "get_break",
    "create_break",
    "update_break",
    "delete_break",
    "sync_occurrences",
]

#: Таймзона на случай занятия без слота (разовое). Школьная, глобальная.
_FALLBACK_TZ = "Europe/Moscow"

#: День занятия в местном времени — перерыв задаётся датами, а занятие хранится
#: моментом времени, и без приведения к дате границы уезжают на сутки.
_LOCAL_DAY = (
    "(lo.scheduled_at AT TIME ZONE COALESCE(ls.timezone, '" + _FALLBACK_TZ + "'))::date"
)


async def list_breaks(
    db: AsyncSession, *, student_id: Optional[int] = None
) -> list[dict]:
    """Перерывы; без указания ученика — все, ближайшие сверху."""
    rows = (
        await db.execute(
            text(
                """
                SELECT b.id, b.student_id, u.full_name, b.starts_on, b.ends_on,
                       b.note, b.created_at,
                       (SELECT count(*)
                          FROM lesson_occurrence_participant p
                          JOIN lesson_occurrence lo ON lo.id = p.occurrence_id
                          LEFT JOIN lesson_slot ls ON ls.id = lo.slot_id
                         WHERE p.student_id = b.student_id
                           AND p.status = 'on_break'
                           AND """
                + _LOCAL_DAY
                + """ BETWEEN b.starts_on AND b.ends_on) AS paused_lessons
                  FROM student_break b
                  JOIN users u ON u.id = b.student_id
                 WHERE (CAST(:student_id AS int) IS NULL OR b.student_id = :student_id)
                 ORDER BY b.starts_on DESC, u.full_name
                """
            ),
            {"student_id": student_id},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


async def get_break(db: AsyncSession, break_id: int) -> Optional[dict]:
    row = (
        await db.execute(
            text(
                "SELECT b.id, b.student_id, u.full_name, b.starts_on, b.ends_on, "
                "       b.note, b.created_at "
                "  FROM student_break b JOIN users u ON u.id = b.student_id "
                " WHERE b.id = :id"
            ),
            {"id": break_id},
        )
    ).first()
    return dict(row._mapping) if row is not None else None


async def student_exists(db: AsyncSession, student_id: int) -> bool:
    """Действующий ученик — перерыв заводится только ему.

    Проверка по роли, а не только по существованию строки: без неё адрес принимал
    бы любой `users.id` и превращался в способ узнать, кто есть в школе.
    """
    row = (
        await db.execute(
            text(
                "SELECT 1 FROM users u "
                "  JOIN user_roles ur ON ur.user_id = u.id "
                "  JOIN roles r ON r.id = ur.role_id AND r.name = 'student' "
                " WHERE u.id = :id AND u.is_active AND u.blocked_at IS NULL"
            ),
            {"id": student_id},
        )
    ).first()
    return row is not None


async def create_break(
    db: AsyncSession,
    *,
    student_id: int,
    starts_on: date,
    ends_on: date,
    note: Optional[str],
    created_by: Optional[int],
) -> int:
    row = (
        await db.execute(
            text(
                "INSERT INTO student_break (student_id, starts_on, ends_on, note, created_by) "
                "VALUES (:s, :from, :to, :note, :by) RETURNING id"
            ),
            {
                "s": student_id,
                "from": starts_on,
                "to": ends_on,
                "note": note,
                "by": created_by,
            },
        )
    ).one()
    await sync_occurrences(db, student_id=student_id)
    await db.commit()
    return int(row.id)


async def update_break(
    db: AsyncSession,
    *,
    break_id: int,
    starts_on: date,
    ends_on: date,
    note: Optional[str],
) -> Optional[int]:
    """Сдвинуть или переименовать перерыв. Возвращает id ученика либо None."""
    row = (
        await db.execute(
            text(
                "UPDATE student_break SET starts_on = :from, ends_on = :to, "
                "       note = :note, updated_at = now() "
                " WHERE id = :id RETURNING student_id"
            ),
            {"id": break_id, "from": starts_on, "to": ends_on, "note": note},
        )
    ).first()
    if row is None:
        return None
    await sync_occurrences(db, student_id=row.student_id)
    await db.commit()
    return int(row.student_id)


async def delete_break(db: AsyncSession, *, break_id: int) -> Optional[int]:
    """Снять перерыв. Занятия, которые он гасил, возвращаются в расписание."""
    row = (
        await db.execute(
            text("DELETE FROM student_break WHERE id = :id RETURNING student_id"),
            {"id": break_id},
        )
    ).first()
    if row is None:
        return None
    await sync_occurrences(db, student_id=row.student_id)
    await db.commit()
    return int(row.student_id)


async def sync_occurrences(db: AsyncSession, *, student_id: int) -> tuple[int, int]:
    """Привести занятия ученика в соответствие с его перерывами.

    Считается от ТЕКУЩЕГО набора перерывов, а не от одного изменённого. Иначе при
    двух пересекающихся перерывах снятие одного вернуло бы в расписание дни,
    которые всё ещё закрыты вторым.

    Возвращает (погашено, возвращено).
    """
    paused = await db.execute(
        text(
            """
            UPDATE lesson_occurrence_participant p
               SET status = 'on_break', updated_at = now()
              FROM lesson_occurrence lo
              LEFT JOIN lesson_slot ls ON ls.id = lo.slot_id
             WHERE p.occurrence_id = lo.id
               AND p.student_id = :s
               AND p.status = 'scheduled'
               AND EXISTS (
                     SELECT 1 FROM student_break b
                      WHERE b.student_id = :s
                        AND """
            + _LOCAL_DAY
            + """ BETWEEN b.starts_on AND b.ends_on
                   )
            """
        ),
        {"s": student_id},
    )
    restored = await db.execute(
        text(
            """
            UPDATE lesson_occurrence_participant p
               SET status = 'scheduled', updated_at = now()
              FROM lesson_occurrence lo
              LEFT JOIN lesson_slot ls ON ls.id = lo.slot_id
             WHERE p.occurrence_id = lo.id
               AND p.student_id = :s
               AND p.status = 'on_break'
               AND NOT EXISTS (
                     SELECT 1 FROM student_break b
                      WHERE b.student_id = :s
                        AND """
            + _LOCAL_DAY
            + """ BETWEEN b.starts_on AND b.ends_on
                   )
            """
        ),
        {"s": student_id},
    )
    return paused.rowcount, restored.rowcount
