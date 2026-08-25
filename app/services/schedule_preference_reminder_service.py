"""Напоминания тем, кто не оставил пожеланий по расписанию (tsk-674, фаза 1).

**Зачем отдельный канал.** В кабинете напоминание уже висит полосой на каждом
экране, и кнопкой его не закрыть. Но ученик, который до 30 августа ни разу не
зайдёт в кабинет, не увидит и её — а опрос нужен со ВСЕХ. Решение оператора
2026-08-25: добавить второй канал, Telegram.

**Как устроено.** Новых труб не строится. Напоминание кладётся строкой в
`notifications` — тот же inbox, откуда student-бот TG_LMS уже забирает
напоминания о занятиях (tsk-431). Оттуда же его видит ученик в кабинете на
`/me/notifications`. То есть одна запись обслуживает сразу два канала.

**Почему не чаще раза в двое суток.** «Напоминать, пока не заполнено» — это
про настойчивость, а не про долбёжку: школа уже знает, что уведомление, которое
раздражает, читают ровно так же, как то, которое не приходит (tsk-591,
tsk-652). Отсрочка считается по последнему напоминанию ЭТОГО вида у этого
ученика, поэтому повторный запуск прохода в тот же день не создаёт второй
строки.

**Молчание = все ответили.** Проход ничего не пишет, если молчащих не осталось.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.services import inbox_service

logger = logging.getLogger(__name__)

#: Ключ advisory-lock прохода. Не пересекается с соседними тиками: начисления —
#: 0x43485247 ("CHRG"), эскалации — 0x59365453 ("Y6TS"), ссылки — 0x4C494E4B.
_REMINDER_LOCK_KEY = 0x50524546  # ascii "PREF"

#: Как часто проходит автоматический тик. Сутки: напоминание раз в двое суток
#: на человека держится отсрочкой, а суточный проход ловит тех, у кого она
#: истекла, и тех, кто зарегистрировался вчера.
_TICK_INTERVAL_HOURS = 24

_scheduler: Optional[AsyncIOScheduler] = None

#: Вид уведомления. Отдельный от `lesson_reminder`: у бота для них разные
#: тексты, а у ученика в кабинете — разный смысл.
REMINDER_KIND = "schedule_preference_reminder"

#: Через сколько часов можно напомнить тому же человеку снова.
DEFAULT_COOLDOWN_HOURS = 48

_TITLE = "Расскажите, когда вам удобно заниматься осенью"

#: Текст один на оба канала: бот отправляет его как сообщение, кабинет
#: показывает как уведомление. Про переезд времени сказано прямо — это главное,
#: что человек должен узнать до того, как выберет часы.
_BODY = (
    "Осенью занятия идут с понедельника по четверг с 12:00 до 19:00 и в субботу "
    "с 09:00 до 14:00 по московскому времени. Утренние занятия в 10:00 и 11:00 "
    "переезжают, поэтому расписание собирается заново.\n\n"
    "Откройте «Удобное время занятий» и отметьте часы, в которые вам удобно: "
    "по этим ответам методист соберёт расписание. Ответить нужно до 30 августа."
)

_LINK = "https://learn.victor-komlev.ru/me/schedule"


async def list_silent(db: AsyncSession) -> list[dict[str, Any]]:
    """Кто из аудитории опроса ещё не оставил пожеланий.

    Условие аудитории повторяет `schedule_preference_service._AUDIENCE_FROM`
    намеренным дублем в одном месте — здесь нужен ещё и `tg_id`, а расширять
    ради этого общую выборку значило бы тащить лишнюю колонку во все её
    остальные вызовы.
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT u.id, u.full_name, u.tg_id
                  FROM users u
                  JOIN user_roles ur ON ur.user_id = u.id
                  JOIN roles r ON r.id = ur.role_id AND r.name = 'student'
                  LEFT JOIN (
                      SELECT ss.student_id, sp.code
                        FROM student_subscription ss
                        JOIN subscription_plan sp ON sp.id = ss.plan_id
                       WHERE ss.ends_on IS NULL
                  ) cur ON cur.student_id = u.id
                  LEFT JOIN student_schedule_preference pref ON pref.student_id = u.id
                 WHERE u.is_active
                   AND (cur.code IS NULL OR cur.code NOT IN ('alumni', 'demo'))
                   AND pref.id IS NULL
                 ORDER BY u.id
                """
            )
        )
    ).fetchall()
    return [{"id": int(r[0]), "full_name": r[1], "tg_id": r[2]} for r in rows]


async def enqueue_reminders(
    db: AsyncSession,
    *,
    cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
    dry_run: bool = False,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """Положить напоминание каждому молчащему, кому давно не напоминали.

    :param cooldown_hours: сколько часов не трогать того, кому уже написали.
    :param dry_run: только посчитать, ничего не записывая.
    :param limit: ограничить число адресатов за проход (страховка от ошибки в
        условии аудитории: разослать полсотни лишних сообщений живым людям
        нельзя отменить).
    :returns: сводка прохода — кому положили, кого пропустили по отсрочке.
    """
    silent = await list_silent(db)
    if not silent:
        logger.info("tsk-674: молчащих нет, напоминать некому")
        return {"silent_total": 0, "queued": 0, "skipped_cooldown": 0, "students": []}

    queued: list[int] = []
    skipped: list[int] = []

    for student in silent:
        if limit is not None and len(queued) >= limit:
            break
        recent = (
            await db.execute(
                text(
                    "SELECT 1 FROM notifications "
                    " WHERE user_id = :uid AND kind = :kind "
                    "   AND modified_at > now() - make_interval(hours => :hours) "
                    " LIMIT 1"
                ),
                {"uid": student["id"], "kind": REMINDER_KIND, "hours": cooldown_hours},
            )
        ).first()
        if recent is not None:
            skipped.append(student["id"])
            continue

        if not dry_run:
            # Через `inbox_service`, а не прямым INSERT: у `notifications` есть
            # обязательная колонка `content` без умолчания, и прямая вставка
            # мимо этой точки упала бы на записи (поймано чтением схемы до
            # записи, протокол /db-check).
            await inbox_service.create_for_user(
                db,
                user_id=student["id"],
                kind=REMINDER_KIND,
                title=_TITLE,
                content=_BODY,
                payload={"url": _LINK, "deadline": "2026-08-30"},
                created_by=None,
            )
        queued.append(student["id"])

    if not dry_run:
        await db.commit()

    logger.info(
        "tsk-674: напоминание о пожеланиях — молчащих %s, положено %s, "
        "пропущено по отсрочке %s%s",
        len(silent),
        len(queued),
        len(skipped),
        " (пробный прогон)" if dry_run else "",
    )
    return {
        "silent_total": len(silent),
        "queued": len(queued),
        "skipped_cooldown": len(skipped),
        "students": queued,
    }


# ------------------------------------------------------------------ суточный проход


async def reminder_tick() -> dict[str, Any]:
    """Один автоматический проход: напомнить всем, у кого истекла отсрочка.

    Лок берётся на весь проход: на проде приложение крутится несколькими
    worker'ами, и без него каждый написал бы молчащим своё напоминание — а это
    сообщения живым людям, задвоение видно сразу и выглядит как поломка.
    """
    async with async_session_factory() as db:
        if not await _try_lock(db):
            logger.debug("tsk-674: проход напоминаний уже идёт в другом worker'е")
            return {"skipped": True}
        return await enqueue_reminders(db)


async def _try_lock(db: AsyncSession) -> bool:
    """Транзакционный advisory-lock: уходит сам, в том числе при падении."""
    got = await db.execute(
        text("SELECT pg_try_advisory_xact_lock(:k) AS locked"),
        {"k": _REMINDER_LOCK_KEY},
    )
    return bool(got.scalar())


async def _safe_tick() -> None:
    """Обёртка для планировщика: упавший проход не роняет остальные задачи.

    Молчать нельзя — след в логе остаётся всегда, иначе отказ неотличим от
    прогона, в котором просто некому было напоминать.
    """
    try:
        await reminder_tick()
    except Exception:
        logger.exception("tsk-674: проход напоминаний упал — за этот раз никому не написано")


def start_scheduler() -> None:
    """Поднять суточный проход напоминаний."""
    global _scheduler
    if _scheduler is not None:
        return

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        _safe_tick,
        IntervalTrigger(hours=_TICK_INTERVAL_HOURS),
        id="schedule_preference_reminder_tick",
        max_instances=1,
        # Пропущенные прогоны не догоняем пачкой: результат одинаковый, а
        # человек получил бы три одинаковых сообщения подряд.
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "tsk-674: напоминания о пожеланиях запущены, интервал %s ч", _TICK_INTERVAL_HOURS
    )


def stop_scheduler() -> None:
    """Остановить проход при остановке приложения."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
