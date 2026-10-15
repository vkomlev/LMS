"""tsk-1361: фоновая сверка переопределений обязательности по правилам.

Раз в интервал для каждого ученика с действующим зачислением правила
(прощение нового, сокращение программы) приводят его строки в
`student_requirement_override` к текущему расчёту.

Паттерн тот же, что у `course_dependency_state_cron_service` (tsk-541/626):
сторож одного worker'а — отдельная сессия с транзакционной advisory-блокировкой,
работа — в своей сессии с коммитом после каждого ученика, ученики по
возрастанию id. Соединение не держится во время ожидания: тик идёт подряд и
закрывается (урок tsk-1250 — пул соединений).
"""
from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.cron_registry import register_interval_job
from app.db.session import async_session_factory
from app.services.content_grace_service import invalidate_grace_cache
from app.services.requirement_override_service import sync_rule_overrides

logger = logging.getLogger("app.requirement_override_sync_cron")

# ascii "RQOV" — не пересекается с соседними ключами (CDST 0x43445354 и др.).
_LOCK_KEY = 0x52514F56

_scheduler: Optional[AsyncIOScheduler] = None


async def requirement_override_sync_tick(
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> dict:
    """Один проход по всем ученикам с действующими зачислениями."""
    factory = session_factory or async_session_factory
    summary = {"locked": False, "students": 0, "inserted": 0, "revoked": 0, "failed": 0}

    async with factory() as guard:
        got = await guard.execute(
            text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _LOCK_KEY}
        )
        if not bool(got.scalar()):
            logger.debug("tsk-1361: тик пропущен — работу делает другой worker")
            return summary
        summary["locked"] = True

        async with factory() as db:
            student_ids = (
                await db.execute(
                    text(
                        "SELECT DISTINCT user_id FROM user_courses "
                        " WHERE is_active = true ORDER BY user_id"
                    )
                )
            ).scalars().all()
            for student_id in student_ids:
                try:
                    res = await sync_rule_overrides(db, int(student_id))
                    await db.commit()
                except Exception:
                    # Сбой одного ученика не должен останавливать остальных;
                    # следующий тик повторит его.
                    await db.rollback()
                    summary["failed"] += 1
                    logger.exception("tsk-1361: сверка ученика %s упала", student_id)
                    continue
                finally:
                    invalidate_grace_cache(db)
                summary["students"] += 1
                summary["inserted"] += res.inserted
                summary["revoked"] += res.revoked

        logger.info(
            "tsk-1361 requirement_override_sync done students=%s inserted=%s revoked=%s failed=%s",
            summary["students"], summary["inserted"], summary["revoked"], summary["failed"],
        )
        # Коммит, а не откат сторожа — см. course_dependency_state_cron_service.
        await guard.commit()
    return summary


def start_scheduler() -> Optional[AsyncIOScheduler]:
    """Поднять периодический тик (если включён настройкой)."""
    global _scheduler
    settings = Settings()
    if not settings.requirement_override_sync_cron_enabled:
        logger.info("tsk-1361: сверка переопределений выключена (REQUIREMENT_OVERRIDE_SYNC_CRON_ENABLED)")
        return None
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    scheduler = AsyncIOScheduler(timezone="UTC")
    register_interval_job(
        scheduler,
        requirement_override_sync_tick,
        job_id="tsk1361_requirement_override_sync",
        startup_delay_min=settings.requirement_override_sync_cron_startup_delay_min,
        minutes=int(settings.requirement_override_sync_cron_interval_min),
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "tsk-1361 requirement_override_sync scheduler started: interval=%smin",
        settings.requirement_override_sync_cron_interval_min,
    )
    return scheduler


def stop_scheduler() -> None:
    """Остановить тик."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("tsk-1361 requirement_override_sync scheduler stopped")
    _scheduler = None
