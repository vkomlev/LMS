"""Y-6 Stage 4: APScheduler tick для эскалации залежавшихся проверок.

Триггер по timeout: pending review (`checked_at IS NULL`) старше
ESCALATION_TIMEOUT_HOURS → push методисту через `methodist_notify_service`.

Multi-worker safety: APScheduler работает в каждом gunicorn-worker'е
независимо. Чтобы избежать дубликата tick'а используется PG advisory lock
(`pg_try_advisory_lock`) — non-blocking, безопасный по shutdown'ам.
Только один worker за tick делает реальную работу; остальные мгновенно
отступают.

Схема развёртывания:
- Pre-deploy: убедиться что в `.env` есть REVIEW_PASS_THRESHOLD_RATIO,
  ESCALATION_TIMEOUT_HOURS, ESCALATION_CRON_INTERVAL_MIN.
- Lifespan: scheduler стартует при FastAPI startup, gracefully останавливается
  при shutdown.
- Тестирование: cron можно дёрнуть вручную через `escalation_cron_tick()`
  (например, из pytest или admin endpoint в будущем).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text

from app.core.config import Settings
from app.db.session import async_session_factory
from app.services import methodist_notify_service

logger = logging.getLogger("app.escalation")

# Произвольный 64-bit ключ для pg_try_advisory_lock. Зафиксирован в коде +
# задокументирован — не должен пересекаться с другими advisory locks
# в проекте (на 2026-05-04 их нет).
_ESCALATION_LOCK_KEY = 0x59365453  # ascii "Y6TS"

_scheduler: Optional[AsyncIOScheduler] = None


async def escalation_cron_tick() -> dict:
    """Один проход cron'а. Возвращает summary для логов / тестов."""
    settings = Settings()
    timeout_hours = int(settings.escalation_timeout_hours)
    rate_limit_per_day = int(settings.methodist_rate_limit_per_day_per_course)

    summary = {"locked": False, "candidates": 0, "escalated": 0}

    async with async_session_factory() as db:
        # Try advisory lock (non-blocking). Один worker — один tick.
        got_row = await db.execute(
            text("SELECT pg_try_advisory_lock(:k) AS locked"),
            {"k": _ESCALATION_LOCK_KEY},
        )
        got = bool(got_row.scalar())
        if not got:
            logger.debug("escalation_cron_tick: advisory lock taken — skip")
            return summary
        summary["locked"] = True

        try:
            # Найти кандидатов: pending TA/SA_COM, timeout, ещё не escalated.
            cutoff = datetime.now(timezone.utc)
            res = await db.execute(
                text(
                    """
                    SELECT tr.id, tr.task_id, tr.user_id, t.course_id, tr.submitted_at
                    FROM task_results tr
                    JOIN tasks t ON t.id = tr.task_id
                    WHERE tr.checked_at IS NULL
                      AND t.task_content->>'type' IN ('SA_COM','TA')
                      AND tr.submitted_at < (now() - (:h || ' hours')::interval)
                      AND (
                          tr.metrics IS NULL
                          OR (
                              jsonb_typeof(tr.metrics) = 'object'
                              AND NOT (tr.metrics ? 'escalated_at')
                          )
                      )
                    ORDER BY tr.submitted_at ASC
                    LIMIT 100
                    """
                ),
                {"h": str(timeout_hours)},
            )
            rows = res.fetchall()
            summary["candidates"] = len(rows)

            for row in rows:
                rid, task_id, user_id, course_id, submitted_at = row
                try:
                    n = await methodist_notify_service.escalate_pending_timeout(
                        db,
                        result_id=int(rid),
                        task_id=int(task_id),
                        student_id=int(user_id),
                        course_id=int(course_id) if course_id is not None else None,
                        submitted_at=submitted_at,
                        timeout_hours=timeout_hours,
                        rate_limit_per_day=rate_limit_per_day,
                    )
                    if n > 0:
                        summary["escalated"] += 1
                except Exception:
                    logger.exception(
                        "escalation_cron_tick: failed for result_id=%s", rid
                    )
                    # Не валим весь tick — продолжаем для остальных кандидатов

            await db.commit()
            logger.info(
                "escalation_cron_tick done at=%s candidates=%s escalated=%s",
                cutoff.isoformat(),
                summary["candidates"],
                summary["escalated"],
            )
        finally:
            # Освободить advisory lock — даже если raise (хотя мы ловим
            # exceptions выше; финальный raise свидетельствует о баге).
            await db.execute(
                text("SELECT pg_advisory_unlock(:k)"),
                {"k": _ESCALATION_LOCK_KEY},
            )
            await db.commit()

    return summary


def start_scheduler() -> AsyncIOScheduler:
    """Стартовать APScheduler с настроенным interval-job'ом.

    Идемпотентен: повторный вызов вернёт существующий scheduler.
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    settings = Settings()
    interval_min = int(settings.escalation_cron_interval_min)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        escalation_cron_tick,
        trigger=IntervalTrigger(minutes=interval_min),
        id="y6_escalation_cron",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "Y-6 escalation scheduler started: interval=%smin", interval_min
    )
    return scheduler


def stop_scheduler() -> None:
    """Graceful shutdown."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Y-6 escalation scheduler stopped")
    _scheduler = None
