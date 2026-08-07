"""Периодический проход датчика учебных пробелов (tsk-572, фаза 7).

Раз в сутки, а не чаще, и это не экономия: сигнал «нужно повторение» строится на
поведении за недели. Проход каждый час выдавал бы те же самые темы снова и снова
— идемпотентность спасла бы базу от дублей, но не людей от ощущения, что список
живёт своей жизнью.

Выключается настройкой `LEARNING_GAPS_CRON_ENABLED`: датчик влияет на то, что
видят преподаватель и методист, и должен отключаться без развёртывания.
"""
from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import Settings
from app.db.session import async_session_factory
from app.services import learning_gap_signals_service

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


async def learning_gaps_tick() -> None:
    """Один проход датчика.

    Исключение наружу не выпускаем: упавший тик не должен ронять планировщик и
    вместе с ним остальные фоновые задачи. Но и молчать нельзя — след в логе
    остаётся всегда, иначе отказ датчика неотличим от «пробелов не нашлось».
    """
    try:
        async with async_session_factory() as db:
            res = await learning_gap_signals_service.scan_and_create_signals(db)
        logger.info("learning_gaps: тик завершён — %s", res)
    except Exception:
        logger.exception("learning_gaps: тик упал — сигналы за этот прогон не заведены")


def start_scheduler() -> Optional[AsyncIOScheduler]:
    """Поднять суточный проход, если включён настройкой."""
    global _scheduler
    settings = Settings()
    if not getattr(settings, "learning_gaps_cron_enabled", True):
        logger.info("learning_gaps: проход выключен (LEARNING_GAPS_CRON_ENABLED)")
        return None
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    hours = int(getattr(settings, "learning_gaps_cron_interval_hours", 24))
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        learning_gaps_tick,
        trigger=IntervalTrigger(hours=hours),
        id="learning_gaps_tick",
        # Пропущенный прогон не догоняем пачкой: три отложенных прохода подряд
        # дадут одни и те же темы и ничего нового, кроме шума в логе.
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("learning_gaps: проход запущен, интервал %s ч", hours)
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
