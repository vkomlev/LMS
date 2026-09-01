"""tsk-429/435 (Календарь LMS): напоминания + авто-no_show, по КАЖДОМУ
участнику независимо (групповое occurrence может иметь несколько участников
в разных статусах одновременно).

Один APScheduler-тик с двумя SQL-ветками (не два отдельных lock-ключа —
решение по простоте, зафиксировано в декомпозиции tsk-429):

1. **Reminder** — участник в статусе `scheduled`, до начала occurrence
   осталось не больше `LESSON_REMINDER_LEAD_MINUTES`, напоминание ещё не
   отправлено ЭТОМУ участнику (idempotency — по существующей
   `notifications` строке с этим `occurrence_id` в payload И этим
   `user_id` — важно оба условия, иначе в групповом occurrence напоминание
   первому участнику погасило бы напоминания остальным).
2. **No-show** — участник в статусе `scheduled` (не `confirmed` — уже
   подтверждённая явка прошедшим временем не переписывается),
   `occurrence.scheduled_at + LESSON_NO_SHOW_THRESHOLD_MINUTES` уже в
   прошлом → помечаем этого участника `no_show`, пишем
   `attendance_event(action='auto_no_show')`, уведомляем ученика И
   преподавателя (по одному уведомлению на каждого пропустившего участника).

Паттерн периодического тика и advisory-lock — тот же, что
`lesson_occurrence_generator_service.py` (который сам скопирован с Y-6
`escalation_service.py`). Lock-ключ здесь другой (`_LESSON_ATTENDANCE_LOCK_KEY`),
не пересекается ни с Y-6 (`0x59365453`), ни с генератором occurrence
(`0x4C534E43`).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import settings_store
from app.core.config import Settings
from app.db.session import async_session_factory
from app.services import homework_service, inbox_service

logger = logging.getLogger("app.lesson_calendar")

_MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def _format_lesson_time(scheduled_at: datetime) -> str:
    """Человекочитаемое время занятия для текста уведомления (не для payload).

    tsk-449: `scheduled_at.isoformat()` раньше подставлялся в `content` как есть —
    учитель/ученик видел сырой ISO-таймстамп в UTC вместо локального времени.
    """
    return scheduled_at.astimezone(_MOSCOW_TZ).strftime("%d.%m, %H:%M")

# ascii "LSNA" (LeSsoN Attendance) — уникален относительно Y-6 (0x59365453)
# и генератора occurrence (0x4C534E43, "LSNC").
_LESSON_ATTENDANCE_LOCK_KEY = 0x4C534E41

_scheduler: Optional[AsyncIOScheduler] = None


async def _send_reminders(db: AsyncSession, *, lead_minutes: int) -> int:
    cutoff = datetime.now(timezone.utc) + timedelta(minutes=lead_minutes)
    now_utc = datetime.now(timezone.utc)
    res = await db.execute(
        text(
            """
            SELECT lop.id AS participant_id, lop.student_id, lo.id AS occurrence_id,
                   lo.teacher_id, lo.scheduled_at
            FROM lesson_occurrence_participant lop
            JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id
            WHERE lop.status = 'scheduled'
              AND lo.scheduled_at BETWEEN :now AND :cutoff
              AND NOT EXISTS (
                  SELECT 1 FROM notifications n
                  WHERE n.kind = 'lesson_reminder'
                    AND n.user_id = lop.student_id
                    AND (n.payload->>'occurrence_id')::int = lo.id
              )
            """
        ),
        {"now": now_utc, "cutoff": cutoff},
    )
    rows = res.fetchall()
    sent = 0

    # tsk-741: строка про домашнюю работу едет ВНУТРИ этого напоминания, а не
    # отдельной рассылкой. Причина простая: `lesson_reminder` — самый читаемый
    # канал у учеников (309 писем за 30 дней, прочитано 60%; для сравнения
    # «ученик молчит» — 16%), и заводить рядом второй значило бы делить то же
    # внимание надвое. Одним запросом на всю выборку, а не на ученика.
    homework = await homework_service.status_for_students(
        db, student_ids=[int(r.student_id) for r in rows], now=now_utc
    )

    for _participant_id, student_id, occurrence_id, teacher_id, scheduled_at in rows:
        # Решение оператора 01.09: строка появляется, ТОЛЬКО если есть
        # несделанное. Кто всё сделал, лишнего не читает — и похвала, звучащая
        # в каждом напоминании, не обесценивается.
        status = homework.get(int(student_id))
        left = (
            status["assigned_total"] - status["assigned_done"] if status else 0
        )
        # Факт без оценки (решение оператора 01.09): ни похвалы, ни укора, ни
        # «ты отстаёшь». Число подросток прочтёт спокойно, бодрый тон — нет.
        homework_line = (
            f" Домашняя работа: {status['assigned_done']} из "
            f"{status['assigned_total']}."
            if status and left > 0
            else ""
        )
        await inbox_service.create_for_user(
            db,
            user_id=int(student_id),
            kind="lesson_reminder",
            title="Скоро занятие",
            content=(
                f"Занятие начинается {_format_lesson_time(scheduled_at)}."
                f"{homework_line} "
                "Не забудьте подтвердить явку в LMS."
            ),
            payload={
                "occurrence_id": int(occurrence_id),
                "teacher_id": int(teacher_id),
                "scheduled_at": scheduled_at.isoformat(),
                "role": "student",
                # Числа отдельно от текста: бот вправе показать их по-своему,
                # а разбирать строку ему нельзя.
                "homework_done": status["assigned_done"] if status else None,
                "homework_total": status["assigned_total"] if status else None,
            },
            created_by=None,
        )
        sent += 1
    return sent


async def _mark_no_show(db: AsyncSession, *, threshold_minutes: int) -> int:
    now_utc = datetime.now(timezone.utc)
    res = await db.execute(
        text(
            """
            SELECT lop.id AS participant_id, lop.student_id, lo.id AS occurrence_id,
                   lo.teacher_id, lo.scheduled_at
            FROM lesson_occurrence_participant lop
            JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id
            WHERE lop.status = 'scheduled'
              AND lo.scheduled_at + (:threshold || ' minutes')::interval < :now
            """
        ),
        {"threshold": str(threshold_minutes), "now": now_utc},
    )
    rows = res.fetchall()
    marked = 0
    for participant_id, student_id, occurrence_id, teacher_id, scheduled_at in rows:
        await db.execute(
            text(
                "INSERT INTO attendance_event (occurrence_id, actor_user_id, action) "
                "VALUES (:oid, NULL, 'auto_no_show')"
            ),
            {"oid": int(occurrence_id)},
        )
        await db.execute(
            text(
                "UPDATE lesson_occurrence_participant SET status = 'no_show', "
                "updated_at = now() WHERE id = :pid"
            ),
            {"pid": int(participant_id)},
        )
        payload = {
            "occurrence_id": int(occurrence_id),
            "teacher_id": int(teacher_id),
            "student_id": int(student_id),
            "scheduled_at": scheduled_at.isoformat(),
        }
        await inbox_service.create_for_user(
            db,
            user_id=int(student_id),
            kind="lesson_missed",
            title="Занятие пропущено",
            content=f"Вы не подтвердили явку на занятие {_format_lesson_time(scheduled_at)}.",
            payload={**payload, "role": "student"},
            created_by=None,
        )

        # tsk-443: занятие может вести несколько преподавателей совместно —
        # уведомляем ВСЕХ (не только lo.teacher_id, который остался "основным"
        # для обратной совместимости), иначе со-преподаватель не узнает о
        # пропуске ученика на СВОЁМ занятии.
        teacher_ids_res = await db.execute(
            text(
                # tsk-492: is_active — разовая подмена. Подменённый не ведёт это
                # занятие и не должен получать письма о пропусках на нём.
                "SELECT teacher_id FROM lesson_occurrence_teacher "
                "WHERE occurrence_id = :oid AND is_active"
            ),
            {"oid": int(occurrence_id)},
        )
        occurrence_teacher_ids = {row[0] for row in teacher_ids_res.fetchall()} or {int(teacher_id)}
        for occ_teacher_id in occurrence_teacher_ids:
            await inbox_service.create_for_user(
                db,
                user_id=int(occ_teacher_id),
                kind="lesson_missed",
                title="Ученик не пришёл",
                content=f"Ученик не подтвердил явку на занятие {_format_lesson_time(scheduled_at)}.",
                payload={**payload, "role": "teacher"},
                created_by=None,
            )
        marked += 1
    return marked


async def lesson_attendance_cron_tick(
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> dict:
    """Один проход. Возвращает summary для логов/тестов."""
    factory = session_factory or async_session_factory
    settings = Settings()
    lead_minutes = settings_store.get_int("lesson_reminder_lead_minutes")
    threshold_minutes = settings_store.get_int("lesson_no_show_threshold_minutes")

    summary = {"locked": False, "reminders_sent": 0, "no_show_marked": 0}

    async with factory() as db:
        got_row = await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:k) AS locked"),
            {"k": _LESSON_ATTENDANCE_LOCK_KEY},
        )
        got = bool(got_row.scalar())
        if not got:
            logger.debug("lesson_attendance_cron_tick: advisory lock занят — skip")
            return summary
        summary["locked"] = True

        summary["reminders_sent"] = await _send_reminders(db, lead_minutes=lead_minutes)
        summary["no_show_marked"] = await _mark_no_show(
            db, threshold_minutes=threshold_minutes
        )

        await db.commit()
        logger.info(
            "lesson_attendance_cron_tick done reminders_sent=%s no_show_marked=%s",
            summary["reminders_sent"],
            summary["no_show_marked"],
        )

    return summary


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    settings = Settings()
    interval_min = int(settings.lesson_attendance_cron_interval_min)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        lesson_attendance_cron_tick,
        trigger=IntervalTrigger(minutes=interval_min),
        id="tsk429_lesson_attendance_cron",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "tsk-429 lesson_attendance scheduler started: interval=%smin", interval_min
    )
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("tsk-429 lesson_attendance scheduler stopped")
    _scheduler = None
