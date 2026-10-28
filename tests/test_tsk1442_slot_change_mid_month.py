"""tsk-1442 — смена дня занятий в середине месяца не даёт ложных «пропавших».

Прод 28.10: ученика 4623 перевели с четверга (слот 33) на субботу (слот 36).
Старую привязку выключили целиком, новая жила с первого дня СЛОТА (31.08), и
октябрь посчитался по субботам: четыре прошедшие субботы без занятий ушли в
вычет, вышло 600 ₽ вместо 3000.

Проверяем денежные инварианты и то, что снятие со слота закрывает привязку
датой, а не стирает её.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.services import charge_service, lesson_calendar_service
from tests.test_tsk756_past_month_not_rewritten import (
    BEFORE_PAST,
    PAST,
    PAST_LAST_DAY,
    _occurrence,
    _setup,
    _slot,
)

pytestmark = pytest.mark.asyncio

_PAST_DAYS = [PAST + timedelta(days=i) for i in range((PAST_LAST_DAY - PAST).days + 1)]
#: День смены — вторник второй недели: до него в месяце есть понедельники
#: старого слота, после — среды нового.
CHANGE = next(d for d in _PAST_DAYS if d.weekday() == 1 and d.day > 7)
OLD_DAYS = [d for d in _PAST_DAYS if d.weekday() == 0 and d < CHANGE]
NEW_DAYS = [d for d in _PAST_DAYS if d.weekday() == 2 and d >= CHANGE]


async def _close_link(db, *, slot_id: int, student_id: int, last_day: date) -> None:
    """Привязка, закрытая так, как её теперь закрывает снятие со слота."""
    await db.execute(
        text(
            "UPDATE lesson_slot_student SET is_active = false, active_until = :d "
            " WHERE slot_id = :s AND student_id = :u"
        ),
        {"s": slot_id, "u": student_id, "d": last_day},
    )
    await db.commit()


async def _open_from(db, *, slot_id: int, student_id: int, first_day: date) -> None:
    await db.execute(
        text(
            "UPDATE lesson_slot_student SET active_from = :d, "
            "       created_at = CAST(:d AS date) + TIME '12:00' "
            " WHERE slot_id = :s AND student_id = :u"
        ),
        {"s": slot_id, "u": student_id, "d": first_day},
    )
    await db.commit()


async def test_day_change_mid_month_has_no_false_missing(db):
    """Ходил по понедельникам, с середины месяца — по средам. Вычетов нет.

    Ровно случай 4623: оба слота заведены задолго до месяца, ученик ходил на
    каждое занятие своего расписания.
    """
    env = await _setup(db, "swap")
    sid, tid = env["student_id"], env["teacher_id"]
    old_slot = await _slot(db, student_id=sid, teacher_id=tid, weekday=0, active_from=BEFORE_PAST)
    new_slot = await _slot(db, student_id=sid, teacher_id=tid, weekday=2, active_from=BEFORE_PAST)
    await _close_link(db, slot_id=old_slot, student_id=sid, last_day=CHANGE - timedelta(days=1))
    await _open_from(db, slot_id=new_slot, student_id=sid, first_day=CHANGE)
    for day in OLD_DAYS:
        await _occurrence(db, slot_id=old_slot, teacher_id=tid, student_id=sid, day=day)
    for day in NEW_DAYS:
        await _occurrence(db, slot_id=new_slot, teacher_id=tid, student_id=sid, day=day)

    counts = await charge_service.lesson_counts_for_month(db, student_id=sid, period=PAST)
    assert counts.expected == len(OLD_DAYS) + len(NEW_DAYS)
    assert counts.missing == 0, "занятия были в каждый день расписания"
    assert counts.not_started == 0
    assert counts.billable == counts.expected


async def test_link_without_dates_still_drops_out(db):
    """Снятая без даты привязка в расчёт не входит — как до tsk-1442.

    На проде таких 151 строка: дат им миграция не ставит, и суммы их учеников
    меняться не должны.
    """
    env = await _setup(db, "legacy")
    sid, tid = env["student_id"], env["teacher_id"]
    slot_id = await _slot(db, student_id=sid, teacher_id=tid, weekday=0, active_from=BEFORE_PAST)
    await db.execute(
        text("UPDATE lesson_slot_student SET is_active = false WHERE slot_id = :s"),
        {"s": slot_id},
    )
    await db.commit()
    counts = await charge_service.lesson_counts_for_month(db, student_id=sid, period=PAST)
    assert counts.expected == 0


async def test_newcomer_mid_month_still_pays_a_share(db):
    """Начало привязки не срезает дни до прихода: новичок платит долю.

    Знаменатель доли — полный месяц. Обрежь мы его датой привязки, пришедший
    в середине месяца заплатил бы как за весь.
    """
    env = await _setup(db, "newbie")
    sid, tid = env["student_id"], env["teacher_id"]
    slot_id = await _slot(db, student_id=sid, teacher_id=tid, weekday=2, active_from=BEFORE_PAST)
    await _open_from(db, slot_id=slot_id, student_id=sid, first_day=CHANGE)
    for day in NEW_DAYS:
        await _occurrence(db, slot_id=slot_id, teacher_id=tid, student_id=sid, day=day)

    counts = await charge_service.lesson_counts_for_month(db, student_id=sid, period=PAST)
    all_wednesdays = [d for d in _PAST_DAYS if d.weekday() == 2]
    assert counts.expected == len(all_wednesdays)
    assert counts.not_started == len(all_wednesdays) - len(NEW_DAYS)
    assert counts.billable == len(NEW_DAYS)


async def test_transfer_closes_old_link_and_keeps_history(db):
    """Перевод закрывает старую привязку вчерашним днём, новая — с сегодня.

    И обратный перевод заводит новую строку, а не оживляет закрытую: иначе
    период между уходом и возвращением снова стал бы «пропавшими» занятиями.
    """
    env = await _setup(db, "transfer")
    sid, tid = env["student_id"], env["teacher_id"]
    a = await _slot(db, student_id=sid, teacher_id=tid, weekday=0)
    b = int(
        (
            await db.execute(
                text(
                    "INSERT INTO lesson_slot (teacher_id, weekday, start_time, "
                    " duration_minutes, timezone, is_active) "
                    "VALUES (:t, 2, '10:00', 60, 'Europe/Moscow', true) RETURNING id"
                ),
                {"t": tid},
            )
        ).scalar()
    )
    await db.commit()

    today = lesson_calendar_service.school_today()
    await lesson_calendar_service.transfer_slot_participant(
        db, source_slot_id=a, target_slot_id=b, student_id=sid, added_by=None
    )
    await lesson_calendar_service.transfer_slot_participant(
        db, source_slot_id=b, target_slot_id=a, student_id=sid, added_by=None
    )

    rows = (
        await db.execute(
            text(
                "SELECT slot_id, is_active, active_from, active_until "
                "  FROM lesson_slot_student WHERE student_id = :u ORDER BY id"
            ),
            {"u": sid},
        )
    ).all()
    yesterday = today - timedelta(days=1)
    assert [(r.slot_id, r.is_active, r.active_from, r.active_until) for r in rows] == [
        (a, False, None, yesterday),
        (b, False, today, yesterday),
        (a, True, today, None),
    ]
