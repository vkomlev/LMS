"""tsk-429 (Календарь LMS, Фаза 2): напоминания + авто-no_show.

Один APScheduler-тик с двумя SQL-ветками (не два отдельных lock-ключа —
решение по простоте, зафиксировано в декомпозиции tsk-429):

1. **Reminder** — occurrence в статусе `scheduled`, до начала осталось не
   больше `LESSON_REMINDER_LEAD_MINUTES`, напоминание ещё не отправлено
   (idempotency — по существующей `notifications` строке с этим
   `occurrence_id` в payload, не по отдельному маркеру в БД).
2. **No-show** — occurrence в статусе `scheduled` (ученик ещё НЕ ответил;
   `confirmed`/`declined` уже разрешены явкой и не трогаются),
   `scheduled_at + LESSON_NO_SHOW_THRESHOLD_MINUTES` уже в прошлом →
   помечаем `no_show`, пишем `attendance_event(action='auto_no_show')`,
   уведомляем ученика И преподавателя.

Паттерн периодического тика и advisory-lock — тот же, что
`lesson_occurrence_generator_service.py` (который сам скопирован с Y-6
`escalation_service.py`). Lock-ключ здесь другой (`_LESSON_ATTENDANCE_LOCK_KEY`),
не пересекается ни с Y-6 (`0x59365453`), ни с генератором occurrence
(`0x4C534E43`).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.session import async_session_factory
from app.services import inbox_service

logger = logging.getLogger("app.lesson_calendar")

# ascii "LSNA" (LeSsoN Attendance) — уникален относительно Y-6 (0x59365453)
# и генератора occurrence (0x4C534E43, "LSNC").
_LESSON_ATTENDANCE_LOCK_KEY = 0x4C534E41

_scheduler: Optional[AsyncIOScheduler] = None


async def _send_reminders(db: AsyncSession, *, lead_minutes: int) -> int:
    cutoff = datetime.now(timezone.utc) + timedelta(minutes=lead_minutes)
    now_utc = datetime.now(timezone.utc)
    res = await db.execute(
        text(
            """
            SELECT lo.id, lo.student_id, lo.teacher_id, lo.scheduled_at
            FROM lesson_occurrence lo
            WHERE lo.status = 'scheduled'
              AND lo.scheduled_at BETWEEN :now AND :cutoff
              AND NOT EXISTS (
                  SELECT 1 FROM notifications n
                  WHERE n.kind = 'lesson_reminder'
                    AND (n.payload->>'occurrence_id')::int = lo.id
              )
            """
        ),
        {"now": now_utc, "cutoff": cutoff},
    )
    rows = res.fetchall()
    sent = 0
    for occurrence_id, student_id, teacher_id, scheduled_at in rows:
        await inbox_service.create_for_user(
            db,
            user_id=int(student_id),
            kind="lesson_reminder",
            title="Скоро занятие",
            content=(
                f"Занятие начинается {scheduled_at.isoformat()}. "
                "Не забудьте подтвердить явку в LMS."
            ),
            payload={
                "occurrence_id": int(occurrence_id),
                "teacher_id": int(teacher_id),
                "scheduled_at": scheduled_at.isoformat(),
                "role": "student",
            },
            created_by=None,
        )
        sent += 1
    return sent


async def _mark_no_show(db: AsyncSession, *, threshold_minutes: int) -> int:
    now_utc = datetime.now(timezone.utc)
    res = await db.execute(
        text(
            """
            SELECT id, student_id, teacher_id, scheduled_at
            FROM lesson_occurrence
            WHERE status = 'scheduled'
              AND scheduled_at + (:threshold || ' minutes')::interval < :now
            """
        ),
        {"threshold": str(threshold_minutes), "now": now_utc},
    )
    rows = res.fetchall()
    marked = 0
    for occurrence_id, student_id, teacher_id, scheduled_at in rows:
        await db.execute(
            text(
                "INSERT INTO attendance_event (occurrence_id, actor_user_id, action) "
                "VALUES (:oid, NULL, 'auto_no_show')"
            ),
            {"oid": int(occurrence_id)},
        )
        await db.execute(
            text(
                "UPDATE lesson_occurrence SET status = 'no_show', updated_at = now() "
                "WHERE id = :oid"
            ),
            {"oid": int(occurrence_id)},
        )
        payload = {
            "occurrence_id": int(occurrence_id),
            "teacher_id": int(teacher_id),
            "student_id": int(student_id),
            "scheduled_at": scheduled_at.isoformat(),
        }
        await inbox_service.create_for_user(
            db,
            user_id=int(student_id),
            kind="lesson_missed",
            title="Занятие пропущено",
            content=f"Вы не подтвердили явку на занятие {scheduled_at.isoformat()}.",
            payload={**payload, "role": "student"},
            created_by=None,
        )
        await inbox_service.create_for_user(
            db,
            user_id=int(teacher_id),
            kind="lesson_missed",
            title="Ученик не пришёл",
            content=f"Ученик не подтвердил явку на занятие {scheduled_at.isoformat()}.",
            payload={**payload, "role": "teacher"},
            created_by=None,
        )
        marked += 1
    return marked


async def lesson_attendance_cron_tick(
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> dict:
    """Один проход. Возвращает summary для логов/тестов."""
    factory = session_factory or async_session_factory
    settings = Settings()
    lead_minutes = int(settings.lesson_reminder_lead_minutes)
    threshold_minutes = int(settings.lesson_no_show_threshold_minutes)

    summary = {"locked": False, "reminders_sent": 0, "no_show_marked": 0}

    async with factory() as db:
        got_row = await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:k) AS locked"),
            {"k": _LESSON_ATTENDANCE_LOCK_KEY},
        )
        got = bool(got_row.scalar())
        if not got:
            logger.debug("lesson_attendance_cron_tick: advisory lock занят — skip")
            return summary
        summary["locked"] = True

        summary["reminders_sent"] = await _send_reminders(db, lead_minutes=lead_minutes)
        summary["no_show_marked"] = await _mark_no_show(
            db, threshold_minutes=threshold_minutes
        )

        await db.commit()
        logger.info(
            "lesson_attendance_cron_tick done reminders_sent=%s no_show_marked=%s",
            summary["reminders_sent"],
            summary["no_show_marked"],
        )

    return summary


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    settings = Settings()
    interval_min = int(settings.lesson_attendance_cron_interval_min)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        lesson_attendance_cron_tick,
        trigger=IntervalTrigger(minutes=interval_min),
        id="tsk429_lesson_attendance_cron",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "tsk-429 lesson_attendance scheduler started: interval=%smin", interval_min
    )
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("tsk-429 lesson_attendance scheduler stopped")
    _scheduler = None
