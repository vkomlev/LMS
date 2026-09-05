"""tsk-630 — неполный первый месяц считается по доле, а не как полный.

До этой задачи месяц, в который ученик пришёл среди месяца, стоил как целый:
`expected_lessons` берётся по постоянному расписанию на ВЕСЬ месяц независимо от
того, что расписание завели 19 числа (Умеров, август 2026: три занятия, 6 000
рублей). Здесь проверяется второй вычет — «ученик ещё не пришёл» — и то, что он
складывается с перерывом, не задваиваясь на пересечении.

Плюс денежный инвариант из того же разбора: пересчёт месяца обязан подбирать
строки групп, которых резолвер уже не возвращает, иначе смена тарифа оставляет
ученику ДВА начисления за один месяц.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.services import charge_service
from tests.test_tsk505_marketer_pricing import _auth, _new_group, _new_user
from tests.test_tsk511_charges_breaks import (
    MONDAYS,
    MONTH_LAST_DAY,
    PERIOD,
    _charge,
    _setup,
)

pytestmark = pytest.mark.asyncio

#: Месяц расчёта и его понедельники (ровно четыре) берутся из tsk-511: он
#: подбирается будущим, поэтому вычет «прошедший день без занятия» здесь
#: не примешивается к проверяемому вычету «ещё не пришёл».
#: Дни ниже задаются от этих понедельников, а не числами календаря.
DAY_AFTER_FIRST_MONDAY = MONDAYS[0] + timedelta(days=1)
DAY_AFTER_SECOND_MONDAY = MONDAYS[1] + timedelta(days=1)
DAY_BEFORE_MONTH = PERIOD - timedelta(days=15)


async def _joined_on(db, *, student_id: int, day: date) -> None:
    """Сдвинуть дату постановки в расписание — «ученик пришёл в этот день».

    Время ставим полуднем по Москве: расчёт приводит `created_at` к МСК, и
    полночь UTC уехала бы на предыдущие сутки, сдвинув отсечку на день.
    """
    await db.execute(
        text(
            "UPDATE lesson_slot_student "
            "   SET created_at = (CAST(:d AS date) + TIME '12:00') "
            "                    AT TIME ZONE 'Europe/Moscow' "
            " WHERE student_id = :s"
        ),
        {"d": day, "s": student_id},
    )
    await db.commit()


async def _break(db, *, student_id: int, starts_on: date, ends_on: date) -> None:
    await db.execute(
        text(
            "INSERT INTO student_break (student_id, starts_on, ends_on) "
            "VALUES (:s, :f, :t)"
        ),
        {"s": student_id, "f": starts_on, "t": ends_on},
    )
    await db.commit()


async def test_joined_mid_month_pays_only_remaining_lessons(db, client):
    """Пришёл после второго занятия — платит за два понедельника из четырёх."""
    env = await _setup(db, "t630-mid", weekdays=(0,))
    await _joined_on(
        db, student_id=env["student_id"], day=DAY_AFTER_SECOND_MONDAY
    )

    counts = await charge_service.lesson_counts_for_month(
        db, student_id=env["student_id"], period=PERIOD
    )
    assert counts.expected == 4, "знаменатель остаётся месячным"
    assert counts.not_started == 2, "первые два понедельника — до прихода"
    assert counts.on_break == 0
    assert counts.billable == 2

    await charge_service.recalculate_for_student(
        db, student_id=env["student_id"], period=PERIOD
    )
    charge = await _charge(client, env["token"], env["student_id"])
    assert charge is not None
    assert charge["calculated_minor"] == 550000 * 2 // 4
    assert charge["expected_lessons"] == 4
    assert charge["not_started_lessons"] == 2


async def test_joined_before_month_pays_full_price(db, client):
    """Пришёл до месяца — доля не применяется, платит целиком.

    Разница именно этих двух случаев и есть суть задачи: «пришёл 1-го» и
    «пришёл 19-го» обязаны стоить по-разному.
    """
    env = await _setup(db, "t630-old", weekdays=(0,))
    await _joined_on(db, student_id=env["student_id"], day=DAY_BEFORE_MONTH)

    counts = await charge_service.lesson_counts_for_month(
        db, student_id=env["student_id"], period=PERIOD
    )
    assert counts.not_started == 0
    assert counts.billable == 4

    await charge_service.recalculate_for_student(
        db, student_id=env["student_id"], period=PERIOD
    )
    charge = await _charge(client, env["token"], env["student_id"])
    assert charge["calculated_minor"] == 550000
    assert charge["not_started_lessons"] == 0


async def test_joined_mid_month_and_break_add_up(db, client):
    """Пришёл после первого занятия и ушёл в перерыв с третьего — вычитается и то, и другое."""
    env = await _setup(db, "t630-both", weekdays=(0,))
    await _joined_on(db, student_id=env["student_id"], day=DAY_AFTER_FIRST_MONDAY)
    await _break(
        db,
        student_id=env["student_id"],
        starts_on=MONDAYS[2],
        ends_on=MONTH_LAST_DAY,
    )

    counts = await charge_service.lesson_counts_for_month(
        db, student_id=env["student_id"], period=PERIOD
    )
    assert counts.not_started == 1, "первый понедельник — до прихода"
    assert counts.on_break == 2, "третий и четвёртый понедельники — перерыв"
    assert counts.billable == 1, "оплачивается только второй понедельник"

    await charge_service.recalculate_for_student(
        db, student_id=env["student_id"], period=PERIOD
    )
    charge = await _charge(client, env["token"], env["student_id"])
    assert charge["calculated_minor"] == 550000 * 1 // 4


async def test_break_before_arrival_is_not_counted_twice(db, client):
    """Перерыв, целиком пришедшийся на дни до прихода, вычитается один раз.

    Иначе новичок, которому сразу оформили перерыв «задним числом», заплатил бы
    меньше нуля занятий — один и тот же день вычли бы дважды.
    """
    env = await _setup(db, "t630-overlap", weekdays=(0,))
    await _joined_on(db, student_id=env["student_id"], day=DAY_AFTER_FIRST_MONDAY)
    await _break(
        db,
        student_id=env["student_id"],
        starts_on=PERIOD,
        # Перерыв кончается до второго понедельника: он накрывает только
        # первый — тот самый день, который уже вычтен как «до прихода».
        ends_on=MONDAYS[0] + timedelta(days=3),
    )

    counts = await charge_service.lesson_counts_for_month(
        db, student_id=env["student_id"], period=PERIOD
    )
    assert counts.not_started == 1, "первый понедельник — до прихода"
    assert counts.on_break == 0, "он же в перерыве, но вычтен уже как «до прихода»"
    assert counts.billable == 3


async def test_second_slot_added_later_does_not_reset_arrival(db, client):
    """Добавили второй слот среди месяца — доля не включается.

    Боевой случай Терехова (август 2026): один слот с 26.07, второй добавлен
    08.08. Считай мы отсечку по последней привязке — старый ученик получил бы
    скидку за первую неделю просто потому, что ему расширили расписание.
    Отсечка берётся по САМОЙ РАННЕЙ привязке, включая снятые: снятие строки
    статусом (`is_active = false`) её `created_at` не трогает, а повторная
    посадка в тот же слот новую строку не создаёт вовсе — пара
    (слот, ученик) уникальна.
    """
    env = await _setup(db, "t630-relink", weekdays=(0,))
    await _joined_on(db, student_id=env["student_id"], day=DAY_BEFORE_MONTH)
    # Второй слот — тоже понедельник, но посажен уже среди месяца.
    second_slot = (
        await db.execute(
            text(
                "INSERT INTO lesson_slot "
                "(teacher_id, weekday, start_time, duration_minutes, timezone, is_active) "
                "VALUES (:t, 0, '11:00', 60, 'Europe/Moscow', true) RETURNING id"
            ),
            {"t": env["teacher_id"]},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO lesson_slot_student (slot_id, student_id, is_active, created_at) "
            "VALUES (:sl, :s, true, "
            "        (CAST(:d AS date) + TIME '12:00') AT TIME ZONE 'Europe/Moscow')"
        ),
        {"sl": second_slot, "s": env["student_id"], "d": DAY_AFTER_SECOND_MONDAY},
    )
    await db.commit()

    counts = await charge_service.lesson_counts_for_month(
        db, student_id=env["student_id"], period=PERIOD
    )
    assert counts.not_started == 0, "первая постановка была до месяца"
    assert counts.expected == 8, "два слота по понедельникам — восемь занятий"
    assert counts.billable == 8


async def test_recalculate_month_drops_charge_of_abandoned_group(db, client):
    """Пересчёт месяца убирает строку группы, по которой считать уже нечего.

    Воспроизводит боевой случай августа 2026: после смены тарифа у ученика
    оставалась строка прежней группы, и «Пересчитать месяц» добавляла к ней
    вторую — 45 строк вместо 41 и лишние 22 000 рублей.
    """
    env = await _setup(db, "t630-stale", weekdays=(0,))
    orphan_group = await _new_group(db, "t630-orphan", [("Общий", 700000, None, None)])
    await db.execute(
        text(
            "INSERT INTO student_monthly_charge "
            "(student_id, group_id, period, calculated_minor, expected_lessons, "
            " break_lessons, not_started_lessons, status) "
            "VALUES (:s, :g, :p, 700000, 4, 0, 0, 'open')"
        ),
        {"s": env["student_id"], "g": orphan_group, "p": PERIOD},
    )
    await db.commit()

    await charge_service.recalculate_month(db, period=PERIOD)

    rows = (
        await db.execute(
            text(
                "SELECT group_id FROM student_monthly_charge "
                " WHERE student_id = :s AND period = :p"
            ),
            {"s": env["student_id"], "p": PERIOD},
        )
    ).all()
    groups = {int(r.group_id) for r in rows}
    assert orphan_group not in groups, "строка брошенной группы должна исчезнуть"
    assert groups == {env["group_id"]}, "ровно одно начисление за месяц"


def test_prorate_subtracts_both_deductions():
    """Формула доли: оба вычета вычитаются, ниже нуля сумма не уходит."""
    counts = charge_service.ChargeCounts(expected=9, on_break=2, not_started=3)
    assert counts.billable == 4
    assert charge_service._prorate(550000, counts) == 550000 * 4 // 9

    # Вычеты вместе больше месяца — платить не за что, но и не минус.
    drained = charge_service.ChargeCounts(expected=4, on_break=3, not_started=3)
    assert drained.billable == 0
    assert charge_service._prorate(550000, drained) == 0

    # Старая форма вызова без нового поля обязана считать как раньше.
    assert charge_service.ChargeCounts(expected=9, on_break=1).billable == 8
