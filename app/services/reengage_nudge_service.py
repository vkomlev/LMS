"""Мягкий возврат затихших учеников (tsk-1177).

**Зачем.** Ученик, выпавший между занятиями, до сих пор ничего не получал, пока
сам не придёт на урок: бот шлёт только напоминания о занятиях и опрос пожеланий
расписания. Регулярно (≥6 недель из 8) сдают ДЗ 29 % учеников с расписанием
(разбор группы «в», `docs/proposals/2026-09-30-courses-group-v-proposals.md`).

**Что делает.** Раз в сутки находит учеников с действующим расписанием, которые
неделю не сдавали работ и не заходили в кабинет, и кладёт им одно напоминание со
ссылкой «продолжить» (`/me/continue` — SPW сам ведёт на следующее задание). Одна
строка `notifications` обслуживает оба канала: кабинет (`/me/notifications`) и
student-бот TG_LMS (`GET /students/{id}/reengage-nudges/pending`).

**Второй порог (14 дней → преподавателю) здесь НЕ делается** — он уже есть:
`learning_gap_signals_service.find_dropout_risk` (tsk-647, окно 14 дней, те же
исключения по тарифу и перерыву) и доходит до преподавателя через
`/teacher/attention/summary`. Второй сигнал того же смысла только задвоил бы
карточки.

**Кого не трогаем** (решение оператора 2026-09-30): выпускников (`alumni`) и
тестовые учётки (`test`); заодно `demo` — это не ученики школы. Фильтр общий,
`real_student_plan_filter`, а не своя копия списка кодов. Также не трогаем тех,
кто на перерыве (`student_break`), и заблокированных/слитых.

**Рубильник и порог** — настройки школы в кабинете администратора
(`reengage_nudge_enabled`, `reengage_nudge_days`, реестр
`app/core/settings_registry.py`). Рубильник выключен по умолчанию: это сообщения
живым людям. Проверяется в начале КАЖДОГО прохода, а не при старте
планировщика — иначе включение требовало бы перезапуска (тот же приём, что у
`curator_report_cron_service`).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import settings_store
from app.core.config import Settings
from app.core.cron_registry import register_interval_job
from app.db.session import async_session_factory
from app.services import inbox_service
from app.services.learning_gap_signals_service import real_student_plan_filter
from app.services.learning_gaps_service import real_student_results_filter

logger = logging.getLogger(__name__)

#: ascii "RNGN" (ReeNGage Nudge) — не пересекается с соседними ключами:
#: PREF 0x50524546, RTNA 0x52544E41, CHRG 0x43485247, Y6TS 0x59365453.
_NUDGE_LOCK_KEY = 0x524E474E

#: Вид уведомления. Отдельный: у бота и кабинета свой смысл у каждого вида.
NUDGE_KIND = "reengage_nudge"

_TICK_INTERVAL_HOURS = 24
#: tsk-939/940: первый проход вскоре после старта, а не через сутки.
_STARTUP_DELAY_MIN = 10

_TITLE = "Продолжим учиться?"
_BODY = (
    "Вы неделю не заглядывали в кабинет. Ничего страшного — следующее задание "
    "уже ждёт, начать можно с него: это займёт несколько минут.\n\n"
    "Если сейчас перерыв или что-то мешает — напишите преподавателю, "
    "подстроим занятия."
)

_scheduler: Optional[AsyncIOScheduler] = None

_QUIET_SQL = """
SELECT u.id
FROM users u
JOIN user_roles ur ON ur.user_id = u.id
JOIN roles r ON r.id = ur.role_id AND r.name = 'student'
LEFT JOIN student_presence pr ON pr.student_id = u.id
WHERE u.is_active
  AND u.merged_into_user_id IS NULL
  AND u.blocked_at IS NULL
  AND {real_student_plan}
  -- Действующее расписание: напоминаем тем, кто учится со школой сейчас.
  AND EXISTS (
      SELECT 1 FROM lesson_slot_student lss
      JOIN lesson_slot ls ON ls.id = lss.slot_id
      WHERE lss.student_id = u.id AND lss.is_active AND ls.is_active
  )
  -- Перерыв задан датами школы (Москва), сервер живёт в UTC — то же правило,
  -- что в `break_service._LOCAL_DAY`.
  AND NOT EXISTS (
      SELECT 1 FROM student_break b
      WHERE b.student_id = u.id
        AND b.starts_on <= (now() AT TIME ZONE 'Europe/Moscow')::date
        AND b.ends_on >= (now() AT TIME ZONE 'Europe/Moscow')::date
  )
  -- Неделю не сдавал сам (ручные зачёты преподавателя — не его работа).
  AND NOT EXISTS (
      SELECT 1 FROM task_results tr
      WHERE tr.user_id = u.id AND {real_student}
        AND tr.submitted_at > now() - make_interval(days => :days)
  )
  -- И не заходил в кабинет: открыл, читал, но не сдал — уже не «затих».
  AND (pr.last_interaction_at IS NULL
       OR pr.last_interaction_at < now() - make_interval(days => :days))
  -- Не чаще раза в окно: без этого каждый суточный проход писал бы заново.
  AND NOT EXISTS (
      SELECT 1 FROM notifications n
      WHERE n.user_id = u.id AND n.kind = :kind
        AND n.modified_at > now() - make_interval(days => :days)
  )
ORDER BY u.id
"""


async def list_quiet(db: AsyncSession, *, days: int) -> list[int]:
    """ID учеников, которым пора напомнить (см. условия в `_QUIET_SQL`)."""
    sql = _QUIET_SQL.format(
        real_student_plan=real_student_plan_filter("u.id"),
        real_student=real_student_results_filter("tr"),
    )
    rows = (await db.execute(text(sql), {"days": days, "kind": NUDGE_KIND})).fetchall()
    return [int(r[0]) for r in rows]


async def enqueue_nudges(
    db: AsyncSession,
    *,
    days: int,
    dry_run: bool = False,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """Положить напоминание каждому затихшему.

    :param days: сколько дней тишины считается «затих» (и отсрочка повтора).
    :param dry_run: только посчитать, ничего не записывая.
    :param limit: страховка от ошибки в условии: разосланное живым людям не
        отменить.
    :returns: сводка прохода.
    """
    quiet = await list_quiet(db, days=days)
    targets = quiet if limit is None else quiet[:limit]
    if not dry_run:
        url = f"{Settings().public_base_url.rstrip('/')}/me/continue"
        for student_id in targets:
            # Через inbox_service: у `notifications.content` нет умолчания.
            await inbox_service.create_for_user(
                db,
                user_id=student_id,
                kind=NUDGE_KIND,
                title=_TITLE,
                content=_BODY,
                payload={"url": url, "quiet_days": days},
                created_by=None,
            )
        await db.commit()

    # Итог в лог всегда: молчащий проход неотличим от отсутствующего.
    logger.info(
        "tsk-1177: мягкий возврат — затихших %s, положено %s%s",
        len(quiet), len(targets), " (пробный прогон)" if dry_run else "",
    )
    return {"quiet_total": len(quiet), "queued": len(targets), "students": targets}


async def nudge_tick() -> dict[str, Any]:
    """Один автоматический проход под advisory-lock (несколько worker'ов).

    Выключенный в кабинете рубильник — тихий выход без записи.
    """
    if not settings_store.get_bool("reengage_nudge_enabled"):
        return {"disabled": True}
    async with async_session_factory() as db:
        got = await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:k) AS locked"),
            {"k": _NUDGE_LOCK_KEY},
        )
        if not bool(got.scalar()):
            logger.debug("tsk-1177: проход уже идёт в другом worker'е")
            return {"skipped": True}
        return await enqueue_nudges(db, days=settings_store.get_int("reengage_nudge_days"))


async def _safe_tick() -> None:
    """Обёртка для планировщика: упавший проход оставляет след и не роняет остальные."""
    try:
        await nudge_tick()
    except Exception:
        logger.exception("tsk-1177: проход мягкого возврата упал — за этот раз никому не написано")


def start_scheduler() -> Optional[AsyncIOScheduler]:
    """Поднять суточный проход. Поднимается всегда: рубильник — в самом проходе."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    scheduler = AsyncIOScheduler(timezone="UTC")
    register_interval_job(
        scheduler,
        _safe_tick,
        job_id="tsk1177_reengage_nudge_tick",
        startup_delay_min=_STARTUP_DELAY_MIN,
        hours=_TICK_INTERVAL_HOURS,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "tsk-1177: планировщик мягкого возврата поднят, интервал %s ч "
        "(включение и порог — в кабинете администратора)",
        _TICK_INTERVAL_HOURS,
    )
    return scheduler


def stop_scheduler() -> None:
    """Остановить проход при остановке приложения."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
