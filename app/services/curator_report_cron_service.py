"""Недельный отчёт по кураторству: сборка и доставка (tsk-742).

Понедельник, утро, отчёт за прошедшую полную неделю. Не чаще: активность
кураторства — недельная величина по самому уставу («не оставить никого без
внимания дольше недели»), и суточный отчёт мерил бы шум.

**Доставка — тем же контуром, что и сигналы**, а не отдельным каналом. Запись в
`notifications` + экран `/curator/weekly-report`. Заводить под отчёт почту или
своего бота значит завести ещё одно место, куда надо не забывать смотреть, — а
задача ровно про то, чтобы держать в голове меньше.

**Рубильник выключен по умолчанию.** Отчёт читает живой человек, и его
содержание — повод для разговора с преподавателями. Включение — правка
настройки в кабинете, без выката (`curator_weekly_report_enabled`).

**Повтор за ту же неделю не отправляется.** Планировщик просыпается каждый час
и сам решает, наступил ли момент; без защиты от повтора оператор получил бы
двадцать четыре одинаковых отчёта в понедельник.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import settings_store
from app.db.session import async_session_factory
from app.services import curator_activity_service, inbox_service  # noqa: F401

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None

#: Вид записи в `notifications` — по нему же проверяется, что отчёт уже ушёл.
NOTIFICATION_KIND = "curator_weekly_report"

#: Сигнал куратору о нём самом (решение оператора 2026-09-02): отчёт не
#: заканчивается наблюдением. Отдельный вид, а не строка в общем отчёте:
#: адресат другой, и смешивать их значит либо показать куратору чужие цифры,
#: либо утопить его собственный сигнал в сводке по школе.
INACTIVITY_KIND = "curator_inactivity"

#: Во сколько по Москве отправлять. Утро понедельника: отчёт должен лечь на стол
#: до того, как начнётся неделя, а не после неё.
SEND_HOUR_MSK = 9

_MSK = timezone(timedelta(hours=3))


async def _recipients(db: AsyncSession) -> list[int]:
    """Кому идёт отчёт: только владельцу школы.

    **Методисты сюда НЕ входят, и это исправление, а не экономия.** Сперва они
    были в списке — по рассуждению «неразобранные сигналы всё равно их работа».
    На боевых данных это оказалось разглашением: методист у нас — тот же
    преподаватель (Серебрякова: `methodist` + `teacher`), и он получал бы
    сводку с оценкой работы СВОИХ КОЛЛЕГ — сколько кто из них не тронул
    учеников. Оператор просил отчёт «у меня»; раздавать его горизонтально
    между кураторами он не просил, а такие цифры между коллегами — это уже не
    отчёт, а служебная характеристика.

    Куратор своё узнаёт сам и только про себя: молчащему уходит персональный
    сигнал (`_nudge_silent_curators`), в котором чужих чисел нет.
    """
    rows = (await db.execute(text("""
        SELECT DISTINCT ur.user_id
        FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id
        WHERE r.name = 'admin'
    """))).all()
    return [int(r[0]) for r in rows]


async def _already_sent(db: AsyncSession, *, week_start: str) -> bool:
    """Отчёт за эту неделю уже отправлен?"""
    row = (await db.execute(text("""
        SELECT 1 FROM notifications
        WHERE kind = :kind AND payload->>'week_start' = :ws
        LIMIT 1
    """), {"kind": NOTIFICATION_KIND, "ws": week_start})).first()
    return row is not None


async def send_weekly_report(db: AsyncSession, *, force: bool = False) -> dict:
    """Собрать отчёт за прошлую полную неделю и разложить получателям.

    `force=True` — отправить, даже если за эту неделю отчёт уже уходил. Нужен
    для ручного прогона и живой проверки; расписание его не использует.
    """
    report = await curator_activity_service.weekly_report(db)
    week_start = report["week_start"]

    if not force and await _already_sent(db, week_start=week_start):
        logger.info("кураторство: отчёт за неделю %s уже отправлен", week_start)
        return {"sent": 0, "week_start": week_start, "skipped": "уже отправлен"}

    text_body = curator_activity_service.render_report_text(report)
    recipients = await _recipients(db)
    for user_id in recipients:
        await inbox_service.create_for_user(
            db,
            user_id=user_id,
            kind=NOTIFICATION_KIND,
            title=f"Кураторство за неделю {week_start}",
            content=text_body,
            # Числа кладём целиком: через месяц по тексту не пересчитать, а
            # сравнить неделю с неделей нужно будет обязательно.
            payload={
                "week_start": week_start,
                "week_end": report["week_end"],
                "curators": report["curators"],
                "students_without_curator": report["students_without_curator"],
            },
            created_by=None,
        )
    nudged = await _nudge_silent_curators(db, week_start=week_start)
    await db.commit()
    logger.info(
        "кураторство: отчёт за %s разослан — получателей %s, кураторов в отчёте %s, "
        "без куратора учеников %s, сигналов молчащим кураторам %s",
        week_start, len(recipients), len(report["curators"]),
        report["students_without_curator"], nudged,
    )
    return {"sent": len(recipients), "week_start": week_start, "nudged": nudged}


async def _nudge_silent_curators(db: AsyncSession, *, week_start: str) -> int:
    """Сигнал куратору, который несколько недель подряд не тронул никого.

    Решение оператора 2026-09-02: отчёт заканчивается не наблюдением. Владелец
    школы в этом разговоре не участвует — он начинается без него.

    Текст обращён к человеку и называет, что именно от него ждут: сигнал
    «у тебя плохие показатели» ничего не меняет, а «зайди к своим ученикам»
    меняет. Повтор за ту же неделю не отправляется — тем же ключом, что и сам
    отчёт.
    """
    silent = await curator_activity_service.curators_without_coverage(db)
    sent = 0
    for c in silent:
        exists = (await db.execute(text("""
            SELECT 1 FROM notifications
            WHERE kind = :kind AND user_id = :uid AND payload->>'week_start' = :ws
            LIMIT 1
        """), {"kind": INACTIVITY_KIND, "uid": c["curator_id"], "ws": week_start})).first()
        if exists:
            continue
        weeks = int(c["weeks"])
        await inbox_service.create_for_user(
            db,
            user_id=int(c["curator_id"]),
            kind=INACTIVITY_KIND,
            title="Ваши ученики остались без внимания",
            content=(
                f"Уже {weeks} недели подряд по вашим ученикам не было ни одного "
                "действия: ни просмотра, ни ответа, ни проверки. Откройте раздел "
                "«Кураторство» — там видно, к кому идти первым."
            ),
            payload={"week_start": week_start, "weeks": weeks},
            created_by=None,
        )
        sent += 1
    return sent


async def curator_report_tick() -> None:
    """Один проход планировщика.

    Рубильник проверяется В НАЧАЛЕ прохода, а не при поднятии планировщика:
    иначе включение обратно требовало бы перезапуска — то есть ровно того
    выката, которого настройка и избегает.

    Исключение наружу не выпускаем: упавший тик не должен ронять планировщик
    вместе с остальными фоновыми задачами. Но след в логе остаётся всегда —
    молчащий отчёт неотличим от «всё хорошо», а это худшая ошибка здесь.
    """
    if not settings_store.get_bool("curator_weekly_report_enabled"):
        return

    now_msk = datetime.now(_MSK)
    if now_msk.weekday() != 0 or now_msk.hour != SEND_HOUR_MSK:
        return

    try:
        async with async_session_factory() as db:
            res = await send_weekly_report(db)
        logger.info("кураторство: тик отчёта завершён — %s", res)
    except Exception:
        logger.exception("кураторство: тик отчёта упал — за эту неделю сводка не ушла")


def start_scheduler() -> Optional[AsyncIOScheduler]:
    """Поднять часовой опрос. Отправка происходит только в свой час."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        curator_report_tick,
        IntervalTrigger(hours=1),
        id="curator_weekly_report",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info("кураторство: планировщик недельного отчёта поднят")
    return _scheduler


def stop_scheduler() -> None:
    """Остановить планировщик."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
