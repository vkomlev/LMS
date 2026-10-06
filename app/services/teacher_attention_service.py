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
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import review_sla
from app.services.teacher_queue_service import REVIEW_ACL_SQL, mandatory_review_sql

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

    # tsk-1176: работы в обязательной очереди этого преподавателя, которые ждут
    # дольше REVIEW_REMINDER_HOURS. Предикат и ACL те же, что у pending-count и
    # claim-next: напоминание обязано говорить ровно о тех работах, которые
    # преподаватель может взять. Захваченные кем-то не считаем — их уже проверяют.
    now = datetime.now(timezone.utc)
    stale_row = (await db.execute(text(
        f"""
        SELECT count(*) AS cnt, min(tr.submitted_at) AS oldest
        FROM task_results tr
        JOIN tasks t ON t.id = tr.task_id
        WHERE tr.checked_at IS NULL
          AND tr.submitted_at < :stale_before
          AND {mandatory_review_sql('t')}
          AND (tr.review_claim_expires_at IS NULL OR tr.review_claim_expires_at < :now_ts)
          AND {REVIEW_ACL_SQL}
        """  # nosec B608 — фрагменты из закрытого набора литералов модуля очереди
    ), {
        "teacher_id": teacher_id,
        "now_ts": now,
        "stale_before": now - timedelta(hours=review_sla.reminder_hours()),
    })).mappings().one()

    oldest_candidates: list[datetime] = [
        r["oldest"] for r in (gap_row, student_idle, lesson_missed)
        if r is not None and r["oldest"] is not None
    ]

    return {
        "gap_signals_new": int(gap_row["cnt"] or 0),
        "student_idle_unread": int(student_idle["cnt"]) if student_idle else 0,
        "lesson_missed_unread": int(lesson_missed["cnt"]) if lesson_missed else 0,
        "reviews_stale": int(stale_row["cnt"] or 0),
        "reviews_stale_oldest_at": stale_row["oldest"],
        "review_reminder_hours": review_sla.reminder_hours(),
        "oldest_created_at": min(oldest_candidates) if oldest_candidates else None,
    }
