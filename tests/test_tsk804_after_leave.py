"""tsk-804 — вычет за дни ПОСЛЕ ухода ученика и пересчёт перед заморозкой.

Начисление считается на весь месяц вперёд по сетке расписания. У прихода
ученика вычет был с tsk-630, у ухода — не было, и выпуск (tsk-673) замораживал
месяц как есть. На проде это дало 11 500 ₽ выдуманного долга двум ученицам:
одной закрыли сентябрь целиком после единственного занятия 02.09, второй — за
месяц, в котором она не училась ни дня.

Главная пара здесь — **`test_recalculation_after_plan_change_erases_the_row`** и
**`test_leaving_on_first_day_keeps_the_row`**. Первая показывает ловушку: после
смены тарифа считать месяц уже не из чего, и пересчёт удаляет строку вместе с
долгом. Вторая — что порядок «пересчёт до смены тарифа» этого не допускает.
Порознь ни один из них ничего не доказывает.

Вторая по важности — `test_leaving_on_last_day_bills_the_whole_month`: она
держит границу с обратной стороны, чтобы вычет не превратился в способ не
платить за уже проведённые занятия.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.services import charge_service, graduation_service, subscription_service
from tests.test_tsk511_charges_breaks import (
    MONDAYS,
    MONTH_LAST_DAY,
    PERIOD,
    WEDNESDAYS,
    _setup,
)

pytestmark = pytest.mark.asyncio

#: Дни занятий месяца при расписании «понедельник + среда» — 9 штук.
LESSON_DAYS = sorted(MONDAYS + WEDNESDAYS)
#: Полная цена месяца в фикстуре.
FULL_PRICE = 550000


def _billable_by(left_on: date) -> int:
    """Занятий к оплате при уходе в этот день: день ухода входит в оплату."""
    return len([day for day in LESSON_DAYS if day <= left_on])


async def _row(db, student_id: int) -> dict | None:
    row = (
        await db.execute(
            text(
                "SELECT status, calculated_minor, expected_lessons, "
                "       after_leave_lessons, break_lessons, not_started_lessons, "
                "       missing_lessons "
                "  FROM student_monthly_charge "
                " WHERE student_id = :s AND period = :p"
            ),
            {"s": student_id, "p": PERIOD},
        )
    ).mappings().first()
    return dict(row) if row is not None else None


async def _recalculate(db, student_id: int, *, left_on: date | None) -> None:
    """Пересчитать месяц фикстуры с заданным днём ухода."""
    group_id = (
        await db.execute(
            text(
                "SELECT group_id FROM student_monthly_charge "
                " WHERE student_id = :s AND period = :p"
            ),
            {"s": student_id, "p": PERIOD},
        )
    ).scalar()
    await charge_service.recalculate_student_group(
        db,
        student_id=student_id,
        group_id=int(group_id),
        period=PERIOD,
        left_on=left_on,
    )
    await db.commit()


async def _seed(db, tag: str) -> dict:
    """Ученик с расписанием «пн + ср» и посчитанным месяцем на 9 занятий."""
    env = await _setup(db, tag, weekdays=(0, 2))
    await charge_service.recalculate_for_student(
        db, student_id=env["student_id"], period=PERIOD
    )
    row = await _row(db, env["student_id"])
    assert row["expected_lessons"] == 9 and row["calculated_minor"] == FULL_PRICE
    return env


# ─────────────────────────── сам вычет и его границы ─────────────────────────


async def test_leaving_mid_month_bills_only_lessons_up_to_the_leave(db) -> None:
    """Ушедший среди месяца платит за занятия по день ухода, не за весь месяц.

    Это и есть случай Гребневой Полины: 9 занятий по сетке, к уходу прошло
    одно, а долг ей выставили за все девять.
    """
    env = await _seed(db, "t804-mid")
    left_on = LESSON_DAYS[2]

    await _recalculate(db, env["student_id"], left_on=left_on)

    row = await _row(db, env["student_id"])
    billable = _billable_by(left_on)
    assert row["after_leave_lessons"] == 9 - billable
    assert row["expected_lessons"] == 9, "знаменатель доли остаётся месячным"
    assert row["calculated_minor"] == FULL_PRICE * billable // 9


async def test_leaving_on_last_day_bills_the_whole_month(db) -> None:
    """Ушедший в конце месяца платит за месяц полностью.

    Обратная ошибка не менее дорога, чем прямая: вычет не должен становиться
    способом не заплатить за уже проведённые занятия.
    """
    env = await _seed(db, "t804-last")

    await _recalculate(db, env["student_id"], left_on=MONTH_LAST_DAY)

    row = await _row(db, env["student_id"])
    assert row["after_leave_lessons"] == 0
    assert row["calculated_minor"] == FULL_PRICE


async def test_leave_day_itself_is_billable(db) -> None:
    """Занятие в САМ день ухода оплачивается — оно уже состоялось.

    На проде ученица ушла 01.09 в 18:47, а занятие у неё было в тот же день в
    16:00. Считать день ухода невыставляемым значило бы не взять денег за
    занятие, которое школа провела.
    """
    env = await _seed(db, "t804-day")
    first_lesson = LESSON_DAYS[0]

    await _recalculate(db, env["student_id"], left_on=first_lesson)

    row = await _row(db, env["student_id"])
    assert row["after_leave_lessons"] == 8, "вычитается только то, что строго после"
    assert row["calculated_minor"] == FULL_PRICE * 1 // 9


async def test_absence_on_the_last_lesson_is_still_paid(db) -> None:
    """Прогул в день ухода оплачивается: вычет — про уход, а не про явку.

    Правило «не пришёл — это его выбор» (tsk-756) новый вычет не отменяет, и
    превращать его в «не был — не плачу» нельзя: тогда любой прогул стал бы
    бесплатным для всех, а не только для уходящих.
    """
    env = await _seed(db, "t804-noshow")
    first_lesson = LESSON_DAYS[0]

    await _recalculate(db, env["student_id"], left_on=first_lesson)

    row = await _row(db, env["student_id"])
    assert row["missing_lessons"] == 0, (
        "месяц будущий — сверки с фактом нет; занятие числится состоявшимся"
    )
    assert row["calculated_minor"] > 0


async def test_break_and_leave_never_deduct_the_same_day_twice(db) -> None:
    """Перерыв и уход не вычитают один день дважды.

    Ровно так выглядят на проде выпускники, которым перерыв «окончание
    обучения» оформляли руками до появления автомата: перерыв до конца месяца
    ПЛЮС уход накрывают одни и те же дни.
    """
    env = await _seed(db, "t804-both")
    left_on = LESSON_DAYS[3]
    await db.execute(
        text(
            "INSERT INTO student_break (student_id, starts_on, ends_on, note) "
            "VALUES (:s, :f, :t, 'окончание обучения')"
        ),
        {"s": env["student_id"], "f": left_on + timedelta(days=1), "t": MONTH_LAST_DAY},
    )
    await db.commit()

    await _recalculate(db, env["student_id"], left_on=left_on)

    row = await _row(db, env["student_id"])
    deductions = (
        row["break_lessons"]
        + row["after_leave_lessons"]
        + row["not_started_lessons"]
        + row["missing_lessons"]
    )
    assert deductions <= row["expected_lessons"], "вычеты пересеклись"
    assert row["calculated_minor"] == FULL_PRICE * _billable_by(left_on) // 9


async def test_student_who_stays_is_not_touched(db) -> None:
    """Ученику, который никуда не уходит, вычет не приписывается."""
    env = await _seed(db, "t804-stays")

    await _recalculate(db, env["student_id"], left_on=None)

    row = await _row(db, env["student_id"])
    assert row["after_leave_lessons"] == 0
    assert row["calculated_minor"] == FULL_PRICE


# ────────────────────── порядок «пересчёт до смены тарифа» ────────────────────


async def test_recalculation_after_plan_change_erases_the_row(db) -> None:
    """ЛОВУШКА: пересчёт ПОСЛЕ смены тарифа стирает месяц вместе с долгом.

    У «Выпускника» тарифной группы нет. Ученику, ушедшему 1-го числа, новая
    строка подписки перекрывает прежнюю уже на первое число месяца — считать
    становится не из чего, и `recalculate_student_group` удаляет открытую
    строку. Парный тест ниже показывает, что выпуск этого не допускает; без
    этой половины он ничего не доказывает.
    """
    env = await _seed(db, "t804-trap")

    await subscription_service.change_plan(
        db, env["student_id"], "alumni", reason="tsk-804 ловушка"
    )
    await db.commit()
    await charge_service.recalculate_for_student(
        db, student_id=env["student_id"], period=PERIOD
    )

    assert await _row(db, env["student_id"]) is None, (
        "если строка уцелела, ловушки больше нет и парный тест ничего не доказывает"
    )


async def test_leaving_on_first_day_keeps_the_row(db) -> None:
    """Выпуск считает деньги ДО смены тарифа — строка месяца остаётся жива.

    Порядок из tsk-673 (свод → заморозка → снятие) сохраняется целиком,
    пересчёт встаёт перед ним.
    """
    env = await _seed(db, "t804-order")

    await graduation_service.recalculate_on_leave(
        db, env["student_id"], left_on=PERIOD
    )
    await subscription_service.change_plan(
        db, env["student_id"], "alumni", reason="tsk-804 порядок"
    )
    result = await graduation_service.apply(db, env["student_id"], changed_by=None)
    await db.commit()

    row = await _row(db, env["student_id"])
    assert row is not None, "строка месяца обязана пережить выпуск"
    assert row["calculated_minor"] == FULL_PRICE * _billable_by(PERIOD) // 9
    assert result.settlement.due_minor == row["calculated_minor"]


async def test_graduation_freezes_the_recalculated_sum(db) -> None:
    """Замораживается пересчитанная сумма, а не та, что стояла до ухода."""
    env = await _seed(db, "t804-freeze")
    left_on = LESSON_DAYS[1]

    await graduation_service.recalculate_on_leave(
        db, env["student_id"], left_on=left_on
    )
    await subscription_service.change_plan(
        db, env["student_id"], "alumni", reason="tsk-804 заморозка"
    )
    await graduation_service.apply(db, env["student_id"], changed_by=None)
    await db.commit()

    row = await _row(db, env["student_id"])
    assert row["status"] == "closed", "долг обязан пережить суточный пересчёт"
    assert row["calculated_minor"] == FULL_PRICE * _billable_by(left_on) // 9
    assert row["calculated_minor"] < FULL_PRICE


async def test_preview_shows_the_sum_that_will_be_charged(db) -> None:
    """Предпросмотр показывает то, что спишется, и ничего при этом не пишет.

    Иначе маркетолог видел бы на экране одну сумму, а перевод закрывал бы
    другую — у ушедшего среди месяца они расходятся в разы.
    """
    env = await _seed(db, "t804-preview")

    plan = await graduation_service.preview(db, env["student_id"], today=PERIOD)
    await db.commit()

    expected = FULL_PRICE * _billable_by(PERIOD) // 9
    assert plan.settlement.due_minor == expected
    untouched = await _row(db, env["student_id"])
    assert untouched["calculated_minor"] == FULL_PRICE, (
        "предпросмотр не должен оставлять следов в базе"
    )
