"""tsk-1536: материализованный вердикт «успевает к экзамену» для дашборда
методиста.

История, определившая форму решения — два инцидента 504 (17.11.2026):
колонка «успевает/не успевает» выкатывалась трижда (in-request, gather,
in-process кэш + фоновый прогрев), и каждый раз дашборд ложился, потому что
сам расчёт tsk-815 (`homework_volume_service.compute`) на прод-данных занимает
8.5–12.5 с на ученика: ~100 программных учеников — это тысяча секунд тяжёлой
работы БД, которая не влезает ни в 60-с таймаут шлюза, ни в пул из 10–30
соединений, не просаживая весь прод. Решение координатора: расчёт живёт
ТОЛЬКО в фоновом тике и пишет готовые вердикты в `methodist_readiness_cache`;
HTTP-запрос дашборда делает один дешёвый SELECT по этой таблице.

Устройство тика (намеренно консервативное, уроки двух инцидентов):

* Один ученик за раз, ПОСЛЕДОВАТЕЛЬНО. Параллелизм (gather, семафор 2/8)
  проверен на проде — именно он задувал QueuePool. Скорость тику не нужна:
  интервал 2 ч, за тик — не более ``refresh_per_tick`` самых протухших.
* Каждому ученику — своя короткая сессия с коммитом: расчёт одного ученика
  ~10 с, и всё это время его транзакция держит снапшот. Общая сессия на тик
  держала бы снапшот на часы.
* Ошибка по одному ученику логируется и не роняет тик — у остальных
  вердикты всё равно обновятся.
* Сторож — транзакционный advisory lock (паттерн tsk-591/tsk-626): при
  нескольких worker'ах тик идёт в одном процессе, другие выходят сразу.
* Студенты, снявшиеся с программных курсов, из кэша удаляются — иначе
  вердикт по ушедшему с программы «висел» бы вечно.

«Догреть одного ученика» (после занятия, по просьбе методиста): публичная
``refresh_student(session, student_id)`` — тот же расчёт + upsert одной
строкой.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import settings_store
from app.core.config import Settings
from app.core.cron_registry import register_interval_job
from app.db.session import async_session_factory
from app.services import homework_volume_service

logger = logging.getLogger("app.methodist_readiness")

# ascii "MRDY" (Methodist Readiness) — не пересекается с ключами соседних
# тиков: Y-6 (0x59365453), генератор занятий (0x4C534E43), явка (0x4C534E41),
# простой (0x4C49444C), ссылки (0x4C494E4B), кеш состояний курсов (0x43445354),
# вложения (0x41545443), начисления (0x43485247), оценка кода (0x43445256),
# удержание (0x52544E41).
_LOCK_KEY = 0x4D524459

# Разрыв «нужно минус делает» в минутах, ниже которого человек считается
# идущим по плану. Тот же порог, что в сводке преподавателя
# (SPW components/teacher/student-verdict.ts, PACE_GAP_MINUTES_THRESHOLD):
# разница в паре минут внутри погрешности веса, а выглядит как замечание.
_ON_TRACK_GAP_MINUTES = 10

_UPSERT_SQL = text("""
    INSERT INTO methodist_readiness_cache (
        student_id, kind, deadline, on_track, details, computed_at,
        created_at, updated_at
    )
    VALUES (
        :student_id, :kind, :deadline, :on_track, CAST(:details AS json),
        now(), now(), now()
    )
    ON CONFLICT (student_id) DO UPDATE SET
        kind = EXCLUDED.kind,
        deadline = EXCLUDED.deadline,
        on_track = EXCLUDED.on_track,
        details = EXCLUDED.details,
        computed_at = EXCLUDED.computed_at,
        updated_at = now()
""")


def on_track_verdict(plan: Any) -> Optional[bool]:
    """Успевает ли к сроку — правило сводки преподавателя, не своё.

    Минутная шкала (когда вес измерен) с порогом ``_ON_TRACK_GAP_MINUTES``;
    штучная — запасная, без порога: там разрыв целочисленный и «не дотягивает
    1» уже значимо. Данных нет (нет ни штучной пары) — None, «нет данных».
    """
    if (
        plan.target_minutes_per_week is not None
        and plan.fact_minutes_per_week is not None
    ):
        return (
            plan.target_minutes_per_week - plan.fact_minutes_per_week
            < _ON_TRACK_GAP_MINUTES
        )
    if plan.target_per_week is not None and plan.fact_per_week is not None:
        return plan.fact_per_week >= plan.target_per_week
    return None


def _details_from_plan(plan: Any) -> dict[str, Any]:
    return {
        "target_per_week": plan.target_per_week,
        "fact_per_week": plan.fact_per_week,
        "target_minutes_per_week": plan.target_minutes_per_week,
        "fact_minutes_per_week": plan.fact_minutes_per_week,
        "remaining_items": plan.remaining_items,
        "program_tasks_remaining": plan.program_tasks_remaining,
    }


async def refresh_student(
    db: AsyncSession, *, student_id: int, now: Optional[datetime] = None,
) -> bool:
    """Пересчитать вердикт для ОДНОГО ученика и положить в кэш.

    Возвращает True, если ученик записан на программу подготовки (строка
    появилась/обновилась), False — если не записан (строка удалена, если была).
    Ошибка расчёта не поднимается: колонка необязательная, вердикт просто
    останется прежним.
    """
    moment = now or datetime.now(timezone.utc)
    try:
        plan = await homework_volume_service.compute(
            db, student_id=student_id, now=moment,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "tsk-1536: readiness не посчитался для ученика %s", student_id,
            exc_info=True,
        )
        return False

    if plan.program_kind is None or plan.program_deadline is None:
        await db.execute(
            text("DELETE FROM methodist_readiness_cache WHERE student_id = :sid"),
            {"sid": student_id},
        )
        return False

    await db.execute(
        _UPSERT_SQL,
        {
            "student_id": student_id,
            "kind": plan.program_kind,
            "deadline": plan.program_deadline,
            "on_track": on_track_verdict(plan),
            "details": _json_dumps(_details_from_plan(plan)),
        },
    )
    return True


def _json_dumps(data: dict[str, Any]) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, default=str)


# Записанные на программные курсы (активные), с датой последнего расчёта;
# NULLS FIRST — холодные впереди, дальше самые протухшие.
_PROGRAM_STUDENTS_SQL = text("""
    SELECT uc.user_id AS student_id, c.computed_at
    FROM user_courses uc
    LEFT JOIN methodist_readiness_cache c ON c.student_id = uc.user_id
    WHERE uc.is_active = true
      AND uc.course_id = ANY(:program_ids)
    GROUP BY uc.user_id, c.computed_at
    ORDER BY c.computed_at ASC NULLS FIRST
    LIMIT :limit
""")


async def _delete_orphans(db: AsyncSession, program_ids: list[int]) -> int:
    result = await db.execute(
        text(
            "DELETE FROM methodist_readiness_cache c "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM user_courses uc "
            "  WHERE uc.user_id = c.student_id "
            "    AND uc.is_active = true "
            "    AND uc.course_id = ANY(:program_ids))"
        ),
        {"program_ids": program_ids},
    )
    return int(result.rowcount or 0)


async def readiness_cron_tick(
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> dict:
    """Один проход: до ``refresh_per_tick`` самых протухших программных учеников.

    Возвращает summary для логов и тестов.
    """
    factory = session_factory or async_session_factory
    summary: dict[str, Any] = {
        "locked": False, "program_students": 0, "refreshed": 0,
        "failed": 0, "deleted_orphans": 0,
    }

    if not settings_store.get_bool("methodist_readiness_cron_enabled"):
        logger.info("tsk-1536: пересчёт readiness выключен настройкой школы")
        return summary

    settings = Settings()
    per_tick = max(1, settings.methodist_readiness_refresh_per_tick)
    now = datetime.now(timezone.utc)

    oge_ids, ege_ids = homework_volume_service.program_course_ids()
    program_ids = sorted(set(oge_ids) | set(ege_ids))
    if not program_ids:
        # Программы не настроены — и кэш не нужен (предыдущие строки могли
        # остаться от старой настройки; course_id = ANY('{}') вернул бы всё).
        async with factory() as db:
            summary["deleted_orphans"] = await _delete_orphans(db, [-1])
            await db.commit()
        return summary

    async with factory() as guard:
        got = await guard.execute(
            text("SELECT pg_try_advisory_xact_lock(:k) AS locked"),
            {"k": _LOCK_KEY},
        )
        if not bool(got.scalar()):
            logger.debug("tsk-1536: тик пропущен — работу делает другой worker")
            return summary
        summary["locked"] = True

        async with factory() as db:
            rows = (
                await db.execute(
                    _PROGRAM_STUDENTS_SQL,
                    {"program_ids": program_ids, "limit": per_tick},
                )
            ).fetchall()
            student_ids = sorted({int(r.student_id) for r in rows})
            summary["program_students"] = len(student_ids)
            orphans = await _delete_orphans(db, program_ids)
            summary["deleted_orphans"] = orphans
            await db.commit()

        # Расчёты идут своими сессиями внутри сторожа: advisory lock
        # (транзакционный) держится до конца тика — иначе два worker'а могли
        # бы дублировать тяжёлые расчёты и снова задуть пул (инциденты 504).
        # Каждая сессия коммитит сама, сторож же закрывается КОММИТОМ, а не
        # откатом (урок tsk-626): его сейвпоинт внешний, и без этого коммита
        # откат при закрытии сторожа стирал бы уже записанное внутри.
        for sid in student_ids:
            try:
                async with factory() as db:
                    ok = await refresh_student(db, student_id=sid, now=now)
                    await db.commit()
                if ok:
                    summary["refreshed"] += 1
                else:
                    summary["failed"] += 1
            except Exception:  # noqa: BLE001
                summary["failed"] += 1
                logger.warning(
                    "tsk-1536: readiness-тик не справился с учеником %s", sid,
                    exc_info=True,
                )

        await guard.commit()

    if summary["refreshed"] or summary["failed"] or summary["deleted_orphans"]:
        logger.info(
            "tsk-1536 readiness_cron_tick done students=%s refreshed=%s "
            "failed=%s deleted_orphans=%s",
            summary["program_students"], summary["refreshed"],
            summary["failed"], summary["deleted_orphans"],
        )
    return summary


_scheduler: Optional[AsyncIOScheduler] = None


def start_scheduler() -> Optional[AsyncIOScheduler]:
    """Поднять периодический пересчёт (apscheduler, тот же паттерн, что tsk-591)."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    settings = Settings()
    interval_min = int(settings.methodist_readiness_cron_interval_min)
    scheduler = AsyncIOScheduler(timezone="UTC")
    register_interval_job(
        scheduler,
        readiness_cron_tick,
        job_id="tsk1536_methodist_readiness_cron",
        startup_delay_min=settings.methodist_readiness_cron_startup_delay_min,
        minutes=interval_min,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "tsk-1536 readiness scheduler started: interval=%smin per_tick=%s",
        interval_min, settings.methodist_readiness_refresh_per_tick,
    )
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("tsk-1536 readiness scheduler stopped")
    _scheduler = None
