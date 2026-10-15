"""tsk-1361: фоновая сверка переопределений обязательности по правилам.

Два тика:

* **быстрый** (раз в минуту) — только ученики, у которых после последнего
  пересчёта появились сдачи, проверки, пропуски, зачёты материалов, новый план
  объёма, новое зачисление или правка содержимого их курсов
  (`requirement_override_service.stale_students`). Решение оператора 15.10:
  экран должен отражать зачёт сразу, а не через час;
* **полный** (раз в час) — все ученики с действующим зачислением. Страховка
  для того, что по данным не видно: правка связей подкурсов (`course_parents`
  без отметки времени) и правка задания прямым SQL мимо `updated_at`.

Паттерн одного worker'а — как у `course_dependency_state_cron_service`
(tsk-541/626): сторож — отдельная сессия с транзакционной advisory-блокировкой.
Работа — **своя короткая сессия на каждого ученика** с коммитом: соединение
возвращается в пул между учениками (урок tsk-1250 — пул соединений). Итого
сторож держит одно соединение на время прохода (полный — около 8 с на 140
учеников на проде 15.10), работа — одно на время одного ученика.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.cron_registry import register_interval_job
from app.db.session import async_session_factory
from app.services.requirement_override_service import stale_students, sync_rule_overrides

logger = logging.getLogger("app.requirement_override_sync_cron")

# ascii "RQOV" — не пересекается с соседними ключами (CDST 0x43445354 и др.).
# Один ключ на оба тика: полный и быстрый не идут одновременно.
_LOCK_KEY = 0x52514F56

_scheduler: Optional[AsyncIOScheduler] = None

_ALL_ACTIVE_SQL = "SELECT DISTINCT user_id FROM user_courses WHERE is_active = true ORDER BY user_id"


async def _sync_students(
    factory: async_sessionmaker[AsyncSession], student_ids: Sequence[int], summary: dict
) -> None:
    """Пересчитать учеников по одному, каждого в своей сессии."""
    for student_id in student_ids:
        async with factory() as db:
            try:
                res = await sync_rule_overrides(db, int(student_id))
                await db.commit()
            except Exception:
                # Сбой одного ученика не останавливает остальных; следующий
                # тик повторит его (отметка свежести откатилась вместе с ним).
                await db.rollback()
                summary["failed"] += 1
                logger.exception("tsk-1361: сверка ученика %s упала", student_id)
                continue
        summary["students"] += 1
        summary["inserted"] += res.inserted
        summary["revoked"] += res.revoked


async def _tick(
    session_factory: Optional[async_sessionmaker[AsyncSession]], *, full: bool
) -> dict:
    """Общий проход: под блокировкой сторожа выбрать учеников и пересчитать."""
    factory = session_factory or async_session_factory
    summary = {
        "locked": False, "full": full, "students": 0,
        "inserted": 0, "revoked": 0, "failed": 0,
    }
    async with factory() as guard:
        got = await guard.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
        if not bool(got.scalar()):
            logger.debug("tsk-1361: тик пропущен — работу делает другой worker")
            return summary
        summary["locked"] = True

        async with factory() as db:
            if full:
                student_ids = [int(r) for r in (await db.execute(text(_ALL_ACTIVE_SQL))).scalars().all()]
            else:
                student_ids = await stale_students(db)
        await _sync_students(factory, student_ids, summary)

        if full or summary["students"] or summary["failed"]:
            logger.info(
                "tsk-1361 requirement_override_sync done full=%s students=%s inserted=%s "
                "revoked=%s failed=%s",
                full, summary["students"], summary["inserted"], summary["revoked"], summary["failed"],
            )
        # Коммит, а не откат сторожа — см. course_dependency_state_cron_service.
        await guard.commit()
    return summary


async def requirement_override_sync_tick(
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> dict:
    """Полный проход по всем ученикам с действующими зачислениями."""
    return await _tick(session_factory, full=True)


async def requirement_override_fast_tick(
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> dict:
    """Быстрый проход — только ученики с несвежими строками."""
    return await _tick(session_factory, full=False)


def start_scheduler() -> Optional[AsyncIOScheduler]:
    """Поднять оба тика (если включены настройкой)."""
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
    register_interval_job(
        scheduler,
        requirement_override_fast_tick,
        job_id="tsk1361_requirement_override_fast",
        startup_delay_min=settings.requirement_override_sync_cron_startup_delay_min + 1,
        seconds=int(settings.requirement_override_fast_interval_sec),
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "tsk-1361 requirement_override_sync scheduler started: full=%smin fast=%ss",
        settings.requirement_override_sync_cron_interval_min,
        settings.requirement_override_fast_interval_sec,
    )
    return scheduler


def stop_scheduler() -> None:
    """Остановить тики."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("tsk-1361 requirement_override_sync scheduler stopped")
    _scheduler = None
