"""tsk-032: фоновая фиксация вех удержания в `user_achievements`.

**Зачем отдельный тик, а не запись на пути чтения.** Состояние удержания
(серия, объём) — производная величина, она считается на лету при каждом
чтении `/me/retention`. Но ВЕХА — событие: у неё есть `earned_at`, и она не
должна исчезать при обрыве серии. Писать её из GET-запроса нельзя (чтение с
побочным эффектом — тот же класс проблем, что `compute_course_state` в путях
чтения, см. `project_lms_service_side_effects`), а вешать запись на приём
ответа значило бы править все пути записи результата сразу (SA, SA_COM,
ручная проверка, импорт) и всё равно пропустить будущий.

Поэтому: правило одно (`retention_service._is_earned`), а вызывают его двое —
чтение (чтобы ученик видел веху сразу) и этот тик (чтобы веха получила
`earned_at` и осталась навсегда). Расхождения между ними быть не может по
построению — общий код, не две копии правила.

Паттерн периодического тика и advisory-lock — тот же, что у соседей
(`course_dependency_state_cron_service`, `lesson_attendance_cron_service`):
один worker за тик делает работу, остальные отступают мгновенно.
"""
from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.session import async_session_factory
from app.services import retention_service

logger = logging.getLogger("app.retention")

# ascii "RTNA" (ReTeNtion Achievements) — не пересекается с соседними ключами:
# Y-6 (0x59365453), генератор occurrence (0x4C534E43), attendance (0x4C534E41),
# link_audit (0x4C494E4B), course-dependency state (0x43445354).
_RETENTION_ACHIEVEMENTS_LOCK_KEY = 0x52544E41

_scheduler: Optional[AsyncIOScheduler] = None


async def retention_achievements_cron_tick(
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> dict:
    """Один проход: зафиксировать вехи, выполненные активными учениками.

    Возвращает summary для логов и тестов."""
    factory = session_factory or async_session_factory
    summary = {"locked": False, "students": 0, "awarded": 0}

    async with factory() as db:
        got = await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:k) AS locked"),
            {"k": _RETENTION_ACHIEVEMENTS_LOCK_KEY},
        )
        if not bool(got.scalar()):
            logger.debug("tsk-032: тик пропущен — работу делает другой worker")
            return summary
        summary["locked"] = True

        student_ids = await retention_service.list_active_student_ids(db)
        summary["students"] = len(student_ids)
        if not student_ids:
            return summary

        summary["awarded"] = await retention_service.award_pending(
            db, student_ids=student_ids
        )
        await db.commit()
        if summary["awarded"]:
            logger.info(
                "tsk-032 retention_achievements_cron_tick: students=%s awarded=%s",
                summary["students"], summary["awarded"],
            )

    return summary


def start_scheduler() -> Optional[AsyncIOScheduler]:
    """Поднимает периодический тик (если включён настройкой)."""
    global _scheduler
    settings = Settings()
    if not settings.retention_achievements_cron_enabled:
        logger.info(
            "tsk-032: фиксация вех удержания выключена "
            "(RETENTION_ACHIEVEMENTS_CRON_ENABLED)"
        )
        return None
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    interval_min = int(settings.retention_achievements_cron_interval_min)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        retention_achievements_cron_tick,
        trigger=IntervalTrigger(minutes=interval_min),
        id="tsk032_retention_achievements_cron",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "tsk-032 retention_achievements scheduler started: interval=%smin", interval_min
    )
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("tsk-032 retention_achievements scheduler stopped")
    _scheduler = None
