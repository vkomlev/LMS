"""Сводка «требует внимания» для преподавателя (tsk-652).

Не строит новых метрик и не создаёт новых видов уведомлений — только
агрегирует то, что уже пишут tsk-646/647 (`learning_gap_signal`) и
lesson_idle_cron_service/lesson_attendance_cron_service (`notifications`,
kind='student_idle'/'lesson_missed'). Единственный потребитель — TG_LMS
teacher-бот (background-поллер, см. `docs/qa/` разбор tsk-652): механизм и
раньше работал правильно, сигнал просто не доходил до человека, потому что
эти два источника не подтягивались в бот вообще (см. отчёт разведки к tsk-652).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def get_summary(db: AsyncSession, *, teacher_id: int) -> dict:
    """Три счётчика + самый старый элемент среди них.

    `gap_signals_new` — тот же предикат, что и `learning_gap_signal_service
    .list_signals(for_student=True, statuses=("new",))` (см. `/learning-gaps
    /students`): общий на всех преподавателей, сигнал не привязан к
    конкретному teacher_id, пока его кто-то не возьмёт в работу.
    """
    gap_row = (await db.execute(text(
        """
        SELECT count(*) AS cnt, min(created_at) AS oldest
        FROM learning_gap_signal
        WHERE status = 'new' AND student_id IS NOT NULL
        """
    ))).mappings().one()

    notif_rows = (await db.execute(text(
        """
        SELECT kind, count(*) AS cnt, min(modified_at) AS oldest
        FROM notifications
        WHERE user_id = :teacher_id
          AND read_at IS NULL
          AND kind IN ('student_idle', 'lesson_missed')
        GROUP BY kind
        """
    ), {"teacher_id": teacher_id})).mappings().all()

    by_kind = {r["kind"]: r for r in notif_rows}
    student_idle = by_kind.get("student_idle")
    lesson_missed = by_kind.get("lesson_missed")

    oldest_candidates: list[datetime] = [
        r["oldest"] for r in (gap_row, student_idle, lesson_missed)
        if r is not None and r["oldest"] is not None
    ]

    return {
        "gap_signals_new": int(gap_row["cnt"] or 0),
        "student_idle_unread": int(student_idle["cnt"]) if student_idle else 0,
        "lesson_missed_unread": int(lesson_missed["cnt"]) if lesson_missed else 0,
        "oldest_created_at": min(oldest_candidates) if oldest_candidates else None,
    }
