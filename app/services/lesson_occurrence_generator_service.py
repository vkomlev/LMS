"""tsk-428/435 (Календарь LMS): генератор lesson_occurrence из активных
lesson_slot на скользящий горизонт вперёд + синк участников (tsk-435,
групповые слоты).

Паттерн периодического тика — по образцу `app/services/escalation_service.py`
(Y-6 Stage 4): APScheduler + `pg_try_advisory_xact_lock`, multi-worker safe
(gunicorn), non-blocking, освобождается автоматически при commit/rollback.

⚠️ Lock-ключ `_LESSON_CALENDAR_LOCK_KEY` не должен пересекаться с другими
advisory-lock ключами проекта (Y-6 `0x59365453`, Фаза 2
`_LESSON_ATTENDANCE_LOCK_KEY = 0x4C534E41`).

Таймзона: Europe/Moscow не наблюдает переход на летнее время (Россия
зафиксировала постоянное время в 2014); DST-fold/gap для этой зоны не
возникает. Если горизонт когда-либо расширится на зоны с DST — пересмотреть
`iter_occurrence_datetimes` (сейчас `datetime.combine(...).replace(tzinfo=tz)`
не обрабатывает неоднозначные локальные времена перехода).
"""
from __future__ import annotations

import logging
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import settings_store
from app.core.config import Settings
from app.db.session import async_session_factory
from app.models.lesson_slot import LessonSlot
from app.models.lesson_slot_student import LessonSlotStudent
from app.models.lesson_slot_teacher import LessonSlotTeacher

logger = logging.getLogger("app.lesson_calendar")

# ascii "LSNC" (LeSsoN Calendar) — уникален относительно Y-6 (0x59365453).
_LESSON_CALENDAR_LOCK_KEY = 0x4C534E43

_scheduler: Optional[AsyncIOScheduler] = None


def iter_occurrence_datetimes(
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

    tsk-679: у слота может стоять `active_until` — последний день действия
    включительно. За него список не выходит, поэтому генератор просто перестаёт
    создавать занятия, а не требует погасить слот в нужный день вручную.
    tsk-756: и `active_from` — первый день действия. Раньше него занятий тоже
    нет: новая сетка, заведённая заранее, начинается в свой день, а не сразу.

    tsk-587: функция публичная, потому что этими же временами
    `lesson_occurrence_service` подбирает кандидатов для переноса. Общий
    расчёт — не удобство, а требование: занятие, к которому присоединяют
    ученика, ищется по ТОЧНОМУ совпадению времени: разойдись два расчёта хоть
    на минуту — вместо места в группе появилось бы параллельное занятие.
    """
    tz = ZoneInfo(slot.timezone)
    now_local = now_utc.astimezone(tz)
    horizon_end_local = now_local + timedelta(days=horizon_days)

    # tsk-679: слот может действовать по дату («старая сетка доживает август»).
    # Обрезаем горизонт по КОНЦУ последнего дня в поясе слота: `active_until`
    # включительная, занятие 31 августа ещё есть, 1 сентября уже нет.
    active_until = getattr(slot, "active_until", None)
    if active_until is not None:
        end_of_day_local = datetime.combine(
            active_until, dt_time.max, tzinfo=tz
        )
        if end_of_day_local < horizon_end_local:
            horizon_end_local = end_of_day_local

    # tsk-756: у слота есть и дата НАЧАЛА. Слот, заведённый заранее («новая
    # сетка с 1 сентября»), не должен рождать занятия раньше своего первого дня —
    # иначе он появится в календаре ученика тогда, когда ещё действует старый.
    start_local = now_local.date()
    active_from = getattr(slot, "active_from", None)
    if active_from is not None and active_from > start_local:
        start_local = active_from

    days_ahead = (slot.weekday - start_local.weekday()) % 7
    current_date = start_local + timedelta(days=days_ahead)

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
    horizon_days = settings_store.get_int("lesson_occurrence_horizon_days")

    summary = {
        "locked": False, "active_slots": 0, "generated": 0,
        "participants_synced": 0, "teachers_synced": 0,
    }

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
            participants_res = await db.execute(
                select(LessonSlotStudent.student_id).where(
                    LessonSlotStudent.slot_id == slot.id,
                    LessonSlotStudent.is_active.is_(True),
                )
            )
            participant_student_ids = [row[0] for row in participants_res.fetchall()]

            teachers_res = await db.execute(
                select(LessonSlotTeacher.teacher_id).where(
                    LessonSlotTeacher.slot_id == slot.id,
                    LessonSlotTeacher.is_active.is_(True),
                )
            )
            slot_teacher_ids = [row[0] for row in teachers_res.fetchall()] or [slot.teacher_id]

            occurrence_datetimes = iter_occurrence_datetimes(
                slot, horizon_days=horizon_days, now_utc=now_utc
            )
            for scheduled_at in occurrence_datetimes:
                # ON CONFLICT ... DO UPDATE (no-op) вместо DO NOTHING — нужен
                # RETURNING id даже когда occurrence уже существует, чтобы
                # синхронизировать участников независимо от того, был ли этот
                # occurrence создан только что или раньше.
                result = await db.execute(
                    text(
                        """
                        INSERT INTO lesson_occurrence
                            (slot_id, teacher_id, scheduled_at, duration_minutes)
                        VALUES
                            (:slot_id, :teacher_id, :scheduled_at, :duration_minutes)
                        ON CONFLICT (slot_id, scheduled_at)
                            WHERE slot_id IS NOT NULL
                            DO UPDATE SET duration_minutes = EXCLUDED.duration_minutes
                        RETURNING id, (xmax = 0) AS was_inserted
                        """
                    ),
                    {
                        "slot_id": slot.id,
                        "teacher_id": slot.teacher_id,
                        "scheduled_at": scheduled_at,
                        "duration_minutes": slot.duration_minutes,
                    },
                )
                row = result.fetchone()
                occurrence_id, was_inserted = row[0], bool(row[1])
                if was_inserted:
                    summary["generated"] += 1

                for student_id in participant_student_ids:
                    p_result = await db.execute(
                        text(
                            """
                            INSERT INTO lesson_occurrence_participant
                                (occurrence_id, student_id, status)
                            VALUES (:oid, :sid, 'scheduled')
                            ON CONFLICT (occurrence_id, student_id) DO NOTHING
                            RETURNING id
                            """
                        ),
                        {"oid": occurrence_id, "sid": student_id},
                    )
                    if p_result.fetchone() is not None:
                        summary["participants_synced"] += 1

                # tsk-443: совместное ведение — синхронизируем ВСЕХ активных
                # преподавателей слота (не только основного), тем же
                # паттерном, что и участников. Без этого co-преподаватель не
                # увидит occurrence ни в первом тике, ни в последующих.
                for teacher_id in slot_teacher_ids:
                    t_result = await db.execute(
                        text(
                            """
                            INSERT INTO lesson_occurrence_teacher
                                (occurrence_id, teacher_id)
                            VALUES (:oid, :tid)
                            ON CONFLICT (occurrence_id, teacher_id) DO NOTHING
                            RETURNING id
                            """
                        ),
                        {"oid": occurrence_id, "tid": teacher_id},
                    )
                    if t_result.fetchone() is not None:
                        summary["teachers_synced"] += 1

        await db.commit()
        logger.info(
            "lesson_occurrence_generator_tick done at=%s active_slots=%s generated=%s "
            "participants_synced=%s teachers_synced=%s",
            now_utc.isoformat(),
            summary["active_slots"],
            summary["generated"],
            summary["participants_synced"],
            summary["teachers_synced"],
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
