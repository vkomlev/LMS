"""tsk-548 — начисление не должно отставать от расписания.

Сумма месяца считается по постоянному расписанию, но пересчёт звали только при
смене тарифа, ручной цены и перерыва. На проде это дало три завышенных счёта:
ученик перешёл с двух занятий в неделю на одно, а сумма осталась прежней.

Второй случай — слияние учёток: расписание переезжало к живой учётке, а
начисление оставалось на мёртвой. У настоящего ученика долга не было вовсе.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services import charge_service, lesson_calendar_service, user_merge_service
from tests.test_tsk511_charges_breaks import _setup, PERIOD

pytestmark = pytest.mark.asyncio


async def _charge_of(db, student_id: int) -> tuple[int, int] | None:
    """(сумма в копейках, занятий в месяце) открытого начисления."""
    row = (
        await db.execute(
            text(
                "SELECT calculated_minor, expected_lessons FROM student_monthly_charge "
                " WHERE student_id = :s AND status = 'open' ORDER BY period DESC LIMIT 1"
            ),
            {"s": student_id},
        )
    ).first()
    return (int(row.calculated_minor), int(row.expected_lessons)) if row else None


async def _slot(db, *, teacher_id: int, weekday: int) -> int:
    """Слот расписания на указанный день недели.

    Группа у слота не хранится: цена берётся из тарифа ученика, а слот знает
    только преподавателя, день и время.
    """
    return int(
        (
            await db.execute(
                text(
                    "INSERT INTO lesson_slot "
                    "       (teacher_id, weekday, start_time, duration_minutes, timezone, is_active) "
                    "VALUES (:t, :w, '17:00', 60, 'Europe/Moscow', true) RETURNING id"
                ),
                {"t": teacher_id, "w": weekday},
            )
        ).scalar()
    )


async def test_removing_a_slot_lowers_the_bill(db):
    """Ученик перешёл с двух занятий в неделю на одно — сумма обязана упасть.

    Ровно этот случай висел на проде: счёт 5 500 ₽ при расписании на 2 750 ₽.
    """
    env = await _setup(db, "resync-drop", price=550000)
    student_id = env["student_id"]

    slot_a = await _slot(db, teacher_id=env["teacher_id"], weekday=0)
    slot_b = await _slot(db, teacher_id=env["teacher_id"], weekday=2)
    await db.commit()

    await lesson_calendar_service.add_slot_participant(
        db, slot_a, student_id, added_by=None
    )
    await lesson_calendar_service.add_slot_participant(
        db, slot_b, student_id, added_by=None
    )
    two_days = await _charge_of(db, student_id)
    assert two_days is not None, "начисление не завелось при добавлении в расписание"

    await lesson_calendar_service.remove_slot_participant(db, slot_b, student_id)
    one_day = await _charge_of(db, student_id)

    assert one_day is not None
    assert one_day[1] < two_days[1], (
        f"занятий в начислении не убавилось: было {two_days[1]}, стало {one_day[1]}"
    )


async def test_adding_a_slot_raises_the_bill(db):
    """Обратная сторона: добавили день — сумма выросла."""
    env = await _setup(db, "resync-add", price=550000)
    student_id = env["student_id"]

    slot_a = await _slot(db, teacher_id=env["teacher_id"], weekday=1)
    slot_b = await _slot(db, teacher_id=env["teacher_id"], weekday=3)
    await db.commit()

    await lesson_calendar_service.add_slot_participant(
        db, slot_a, student_id, added_by=None
    )
    before = await _charge_of(db, student_id)

    await lesson_calendar_service.add_slot_participant(
        db, slot_b, student_id, added_by=None
    )
    after = await _charge_of(db, student_id)

    assert before is not None and after is not None
    assert after[1] > before[1], "добавили занятие, а месяц остался прежним"


async def test_merge_moves_the_money_to_the_living_account(db):
    """После слияния долг живёт у той учётки, где расписание.

    На проде было наоборот: 5 500 ₽ на слитой учётке без занятий и ноль долга
    у настоящего ученика с двумя занятиями в неделю.
    """
    source_env = await _setup(db, "resync-src", price=550000)
    target_env = await _setup(db, "resync-dst", price=550000)
    source_id, target_id = source_env["student_id"], target_env["student_id"]

    slot = await _slot(db, teacher_id=source_env["teacher_id"], weekday=4)
    await db.commit()
    await lesson_calendar_service.add_slot_participant(db, slot, source_id, added_by=None)
    assert await _charge_of(db, source_id) is not None, "у source нет начисления"

    await user_merge_service.apply_merge(db, source_id, target_id)
    await db.commit()
    await charge_service.recalculate_open_months_for_student(db, student_id=target_id)

    left = (
        await db.execute(
            text(
                "SELECT count(*) AS n FROM student_monthly_charge "
                " WHERE student_id = :s AND status = 'open'"
            ),
            {"s": source_id},
        )
    ).one()
    assert left.n == 0, "на слитой учётке остался призрачный долг"
    assert await _charge_of(db, target_id) is not None, (
        "живая учётка осталась без начисления — счёт выставить некому"
    )


async def test_merge_carries_the_manual_price(db):
    """Ручная цена переезжает: это договорённость с человеком, не свойство строки."""
    source_env = await _setup(db, "resync-price-src", price=550000)
    target_env = await _setup(db, "resync-price-dst", price=550000)
    source_id, target_id = source_env["student_id"], target_env["student_id"]

    await charge_service.set_price_override(
        db, student_id=source_id, group_id=1, price_minor=333000,
        note="договорились", created_by=None,
    )

    await user_merge_service.apply_merge(db, source_id, target_id)
    await db.commit()

    row = (
        await db.execute(
            text("SELECT price_minor FROM student_price_override WHERE student_id = :s"),
            {"s": target_id},
        )
    ).first()
    assert row is not None, "ручная цена потерялась при слиянии"
    assert int(row.price_minor) == 333000
