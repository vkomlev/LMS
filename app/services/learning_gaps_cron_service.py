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

from app.core import settings_store
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
    # tsk-721: рубильник проверяется в НАЧАЛЕ прохода, а не при поднятии
    # планировщика. Иначе включение обратно требовало бы перезапуска — то
    # есть ровно того, от чего задача и избавляет. Выключенный проход
    # просыпается и сразу выходит: работы он не делает.
    if not settings_store.get_bool("learning_gaps_cron_enabled"):
        logger.info("learning_gaps: проход выключен настройкой школы")
        return

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
    # Планировщик поднимается всегда: включён проход или нет, решает сам тик
    # по настройке школы (tsk-721).
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    hours = int(getattr(settings, "learning_gaps_cron_interval_hours", 24))
    delay_min = int(getattr(settings, "learning_gaps_cron_startup_delay_min", 5))
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        learning_gaps_tick,
        trigger=IntervalTrigger(hours=hours),
        id="learning_gaps_tick",
        # tsk-653: первый проход — вскоре после старта, а не через сутки.
        # `IntervalTrigger` без `next_run_time` отсчитывает интервал от момента
        # РЕГИСТРАЦИИ джобы, то есть от последнего рестарта процесса. На проде
        # рестарт (деплой) случается в среднем раз в 2-3 часа — без этой
        # поправки датчик почти никогда не накапливал суток непрерывной работы
        # и реально срабатывал единицы раз за три недели вместо ежедневного
        # прохода (обнаружено при разборе tsk-653: 1 завершённый тик и 23
        # рестарта планировщика за доступное окно логов). Тот же приём уже
        # применён в `llm_chain_check_cron_service` по той же причине. Не в сам
        # момент старта: приложению есть чем заняться в первые секунды.
        next_run_time=_startup_run_at(delay_min),
        # Пропущенный прогон не догоняем пачкой: три отложенных прохода подряд
        # дадут одни и те же темы и ничего нового, кроме шума в логе.
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "learning_gaps: проход запущен, интервал %s ч, первый проход через %s мин",
        hours, delay_min,
    )
    return scheduler


def _startup_run_at(delay_min: int):
    """Момент первого прохода. Вынесено функцией, чтобы тест не ждал минутами."""
    from datetime import datetime, timedelta, timezone as _tz

    return datetime.now(_tz.utc) + timedelta(minutes=max(0, delay_min))


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
