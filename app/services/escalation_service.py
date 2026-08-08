"""Y-6 Stage 4: APScheduler tick для эскалации залежавшихся проверок.

Триггер по timeout: pending review (`checked_at IS NULL`) старше
ESCALATION_TIMEOUT_HOURS → push методисту через `methodist_notify_service`.

Multi-worker safety: APScheduler работает в каждом gunicorn-worker'е
независимо. Чтобы избежать дубликата tick'а используется PG advisory lock
уровня транзакции (`pg_try_advisory_xact_lock`) — non-blocking, безопасный
по shutdown'ам. Lock освобождается автоматически при commit/rollback/обрыве
соединения, поэтому утечка в пул соединений невозможна (ручной unlock не
нужен). Только один worker за tick делает реальную работу; остальные
мгновенно отступают.

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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.session import async_session_factory
from app.services import methodist_notify_service
# tsk-597: единый предикат обязательной очереди (tsk-247). Импортируется, а не
# копируется: своя копия здесь уже разъезжалась с очередью и давала 100%
# ложных срабатываний.
from app.services.teacher_queue_service import mandatory_review_sql

logger = logging.getLogger("app.escalation")

# Произвольный 64-bit ключ для pg_try_advisory_xact_lock. Зафиксирован в коде +
# задокументирован — не должен пересекаться с другими advisory locks
# в проекте (на 2026-05-04 их нет).
_ESCALATION_LOCK_KEY = 0x59365453  # ascii "Y6TS"

_scheduler: Optional[AsyncIOScheduler] = None


async def escalation_cron_tick(
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> dict:
    """Один проход cron'а. Возвращает summary для логов / тестов.

    `session_factory` — точка подмены источника сессий. В проде не
    передаётся: APScheduler зовёт tick без аргументов и берётся глобальная
    фабрика поверх QueuePool. Тесты передают NullPool-фабрику, привязанную
    к своему event loop, иначе соединение из пула прошлого теста
    переиспользуется в новом loop (asyncpg: «attached to a different loop»).
    """
    factory = session_factory or async_session_factory
    settings = Settings()
    timeout_hours = int(settings.escalation_timeout_hours)
    rate_limit_per_day = int(settings.methodist_rate_limit_per_day_per_course)

    summary = {"locked": False, "candidates": 0, "escalated": 0}

    async with factory() as db:
        # Transaction-scoped advisory lock (non-blocking): один worker — один tick.
        # xact-lock освобождается автоматически при commit/rollback/обрыве
        # соединения, поэтому утечка lock'а в пул соединений невозможна
        # (в отличие от session-level pg_try_advisory_lock + ручной unlock).
        got_row = await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:k) AS locked"),
            {"k": _ESCALATION_LOCK_KEY},
        )
        got = bool(got_row.scalar())
        if not got:
            logger.debug("escalation_cron_tick: advisory lock занят — skip")
            return summary
        summary["locked"] = True

        # Найти кандидатов: pending TA/SA_COM, timeout, ещё не escalated.
        cutoff = datetime.now(timezone.utc)
        res = await db.execute(
            text(
                """
                SELECT tr.id, tr.task_id, tr.user_id, t.course_id, tr.submitted_at
                FROM task_results tr
                JOIN tasks t ON t.id = tr.task_id
                WHERE tr.checked_at IS NULL
                  -- tsk-597: ТОТ ЖЕ предикат обязательной очереди, что у списка
                  -- проверки и claim-next. Раньше здесь жила своя ось — «по типу
                  -- задания + is_correct IS TRUE», — и она разъехалась с очередью
                  -- ровно так же, как разъезжались список и claim-next до
                  -- tsk-247. Эскалация обязана звать методиста ТОЛЬКО туда, куда
                  -- он может прийти: если работы нет в обязательной очереди,
                  -- звать его не к чему.
                  --
                  -- Чем это было на проде (замер 2026-08-08): 502 кандидата, из
                  -- них 502 ложных. `SA_COM`/`TBL_COM` с manual_review_required
                  -- = false проверены автоматом, а `is_correct=TRUE` им ставит
                  -- оптимистичный зачёт при сдаче; `checked_at` у них не
                  -- проставляется никогда, потому что вторичная проверка не
                  -- положена. Старая ось видела их «зависшими» и через 48 ч
                  -- звала методиста в очередь, где их нет и быть не должно.
                  --
                  -- Обратная сторона той же ошибки: `SA_COM` с
                  -- manual_review_required=true держит `is_correct=NULL` до
                  -- вердикта преподавателя, под ветку `is_correct IS TRUE` не
                  -- подходил, под ветку `type='SA'` тоже — и настоящий кандидат
                  -- не эскалировался бы НИКОГДА (зеркало tsk-438, где тот же
                  -- разрыв закрыли для SA-manual). На проде такая работа есть:
                  -- №15843, сдана 2026-08-08 12:57.
                  --
                  -- Гибридный режим (tsk-396) внутри общего предиката учтён:
                  -- `partial_auto_check` + `is_correct=FALSE` — законченный
                  -- авто-вердикт «числа не сошлись», работа к преподавателю и
                  -- не шла.
                  AND """
                # nosec B608 — подставляется не пользовательский ввод, а
                # SQL-фрагмент из `teacher_queue_service`, собранный из двух
                # литеральных алиасов ('t', 'tr'). То же обоснование, что у
                # остальных call-site этого предиката.
                + mandatory_review_sql("t", "tr")
                + """
                  AND tr.submitted_at < (now() - (:h || ' hours')::interval)
                  -- tsk-582: пропускаем работу, только если эскалация УЖЕ была.
                  -- Прежнее условие (`metrics IS NULL OR (typeof='object' AND
                  -- NOT ? 'escalated_at')`) отсекало всё, что не объект, а в
                  -- metrics при сдаче ложится НЕ SQL NULL, а JSON-null:
                  -- Pydantic-поле metrics=None сериализуется в json null, для
                  -- него `IS NULL` ложно, а jsonb_typeof даёт 'null'. Такие
                  -- работы не проходили ни одну ветку и не эскалировались
                  -- НИКОГДА (на проде 2026-08-08 — 268 зависших проверок,
                  -- 23 курса, 0 строк с SQL NULL вообще). Тот же класс, что
                  -- tsk-361 (solution_rules = JSON-null мимо IS NULL).
                  -- Форма ниже ловит и SQL NULL, и JSON-null, и массив
                  -- (`[null, {...}]` — след скрипта tsk-210, который дописывал
                  -- metrics конкатенацией без typeof-гарда).
                  --
                  -- Ветка `metrics IS NULL` обязана стоять первой и явно:
                  -- краткое `NOT (jsonb_typeof(...) = 'object' AND ... ? ...)`
                  -- на SQL NULL даёт NULL, а не TRUE (трёхзначная логика), и
                  -- отсекает ровно те строки, ради которых правка делалась.
                  AND (
                      tr.metrics IS NULL
                      OR jsonb_typeof(tr.metrics) <> 'object'
                      OR NOT (tr.metrics ? 'escalated_at')
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

        # commit освобождает transaction-scoped advisory lock
        await db.commit()
        logger.info(
            "escalation_cron_tick done at=%s candidates=%s escalated=%s",
            cutoff.isoformat(),
            summary["candidates"],
            summary["escalated"],
        )

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
