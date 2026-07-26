"""tsk-428 (Календарь LMS, Фаза 1): генератор lesson_occurrence из активных
lesson_slot на скользящий горизонт вперёд.

Паттерн периодического тика — по образцу `app/services/escalation_service.py`
(Y-6 Stage 4): APScheduler + `pg_try_advisory_xact_lock`, multi-worker safe
(gunicorn), non-blocking, освобождается автоматически при commit/rollback.

⚠️ Lock-ключ `_LESSON_CALENDAR_LOCK_KEY` не должен пересекаться с другими
advisory-lock ключами проекта (на 2026-07-26 существует только один — Y-6
`_ESCALATION_LOCK_KEY = 0x59365453`, "Y6TS").

Таймзона: Europe/Moscow не наблюдает переход на летнее время (Россия
зафиксировала постоянное время в 2014); DST-fold/gap для этой зоны не
возникает. Если горизонт когда-либо расширится на зоны с DST — пересмотреть
`_iter_occurrence_datetimes` (сейчас `datetime.combine(...).replace(tzinfo=tz)`
не обрабатывает неоднозначные локальные времена перехода).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.session import async_session_factory
from app.models.lesson_slot import LessonSlot

logger = logging.getLogger("app.lesson_calendar")

# ascii "LSNC" (LeSsoN Calendar) — уникален относительно Y-6 (0x59365453).
_LESSON_CALENDAR_LOCK_KEY = 0x4C534E43

_scheduler: Optional[AsyncIOScheduler] = None


def _iter_occurrence_datetimes(
    slot: LessonSlot,
    *,
    horizon_days: int,
    now_utc: datetime,
) -> list[datetime]:
    """Даты/время будущих occurrence для слота в пределах горизонта (UTC-aware).

    Идемпотентно по построению: всегда считает от текущего `now_utc`, не
    хранит состояние — повторный вызов с тем же `now_utc` даёт тот же список.
    Уже прошедшее сегодня время слота не включается (следующее вхождение —
    через 7 дней).
    """
    tz = ZoneInfo(slot.timezone)
    now_local = now_utc.astimezone(tz)
    horizon_end_local = now_local + timedelta(days=horizon_days)

    days_ahead = (slot.weekday - now_local.weekday()) % 7
    current_date = now_local.date() + timedelta(days=days_ahead)

    results: list[datetime] = []
    while True:
        candidate_local = datetime.combine(current_date, slot.start_time, tzinfo=tz)
        if candidate_local > horizon_end_local:
            break
        if candidate_local >= now_local:
            results.append(candidate_local.astimezone(timezone.utc))
        current_date += timedelta(days=7)
    return results


async def lesson_occurrence_generator_tick(
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> dict:
    """Один проход генератора. Возвращает summary для логов/тестов.

    `session_factory` — точка подмены источника сессий (тесты передают
    NullPool-фабрику, привязанную к своему event loop — см.
    `escalation_cron_tick` docstring про ту же причину).
    """
    factory = session_factory or async_session_factory
    settings = Settings()
    horizon_days = int(settings.lesson_occurrence_horizon_days)

    summary = {"locked": False, "active_slots": 0, "generated": 0}

    async with factory() as db:
        got_row = await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:k) AS locked"),
            {"k": _LESSON_CALENDAR_LOCK_KEY},
        )
        got = bool(got_row.scalar())
        if not got:
            logger.debug("lesson_occurrence_generator_tick: advisory lock занят — skip")
            return summary
        summary["locked"] = True

        now_utc = datetime.now(timezone.utc)
        res = await db.execute(select(LessonSlot).where(LessonSlot.is_active.is_(True)))
        active_slots = list(res.scalars().all())
        summary["active_slots"] = len(active_slots)

        for slot in active_slots:
            occurrence_datetimes = _iter_occurrence_datetimes(
                slot, horizon_days=horizon_days, now_utc=now_utc
            )
            for scheduled_at in occurrence_datetimes:
                result = await db.execute(
                    text(
                        """
                        INSERT INTO lesson_occurrence
                            (slot_id, student_id, teacher_id, scheduled_at,
                             duration_minutes, status)
                        VALUES
                            (:slot_id, :student_id, :teacher_id, :scheduled_at,
                             :duration_minutes, 'scheduled')
                        ON CONFLICT (slot_id, scheduled_at)
                            WHERE slot_id IS NOT NULL
                            DO NOTHING
                        RETURNING id
                        """
                    ),
                    {
                        "slot_id": slot.id,
                        "student_id": slot.student_id,
                        "teacher_id": slot.teacher_id,
                        "scheduled_at": scheduled_at,
                        "duration_minutes": slot.duration_minutes,
                    },
                )
                if result.fetchone() is not None:
                    summary["generated"] += 1

        await db.commit()
        logger.info(
            "lesson_occurrence_generator_tick done at=%s active_slots=%s generated=%s",
            now_utc.isoformat(),
            summary["active_slots"],
            summary["generated"],
        )

    return summary


def start_scheduler() -> AsyncIOScheduler:
    """Стартовать APScheduler с настроенным interval-job'ом. Идемпотентен."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    settings = Settings()
    interval_min = int(settings.lesson_occurrence_cron_interval_min)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        lesson_occurrence_generator_tick,
        trigger=IntervalTrigger(minutes=interval_min),
        id="tsk428_lesson_occurrence_generator_cron",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "tsk-428 lesson_occurrence_generator scheduler started: interval=%smin",
        interval_min,
    )
    return scheduler


def stop_scheduler() -> None:
    """Graceful shutdown."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("tsk-428 lesson_occurrence_generator scheduler stopped")
    _scheduler = None
