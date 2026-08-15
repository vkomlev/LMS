"""tsk-301 Фаза 8: самостоятельная покупка тарифа.

Это цель ×10 всего брифа: человек с улицы платит и получает доступ без участия
оператора. Замыкается круг «оплатил → начислено → доступ» — разорвать его в
любом месте значит либо дать доступ даром, либо взять деньги и не дать ничего.

Проверяется поведение на границах, а не счастливый путь:
 * повтор доставки уведомления не выдаёт тариф дважды и не берёт денег дважды;
 * покупка после порога месяца не выставляет счёт за текущий месяц;
 * самому продаются только тарифы БЕЗ занятий — расписание заводит методист, и
   продавать через кнопку то, что некому выполнить, нельзя.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import subscription_service as subs

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(scope="function")
async def student(db: AsyncSession) -> int:
    return int(
        (
            await db.execute(
                text(
                    "INSERT INTO users (full_name, email, is_active) "
                    "VALUES ('tsk301 покупатель', :e, true) RETURNING id"
                ),
                {"e": f"tsk301-buy-{uuid.uuid4().hex[:12]}@example.test"},
            )
        ).scalar_one()
    )


async def _plan_of(db: AsyncSession, student_id: int) -> str | None:
    return (
        await db.execute(
            text(
                "SELECT p.code FROM student_subscription s "
                "  JOIN subscription_plan p ON p.id = s.plan_id "
                " WHERE s.student_id = :s AND s.ends_on IS NULL"
            ),
            {"s": student_id},
        )
    ).scalar()


async def _payments(db: AsyncSession, student_id: int) -> list[tuple]:
    rows = (
        await db.execute(
            text(
                "SELECT period, amount_minor FROM student_payment "
                " WHERE student_id = :s ORDER BY id"
            ),
            {"s": student_id},
        )
    ).all()
    return [(r.period, r.amount_minor) for r in rows]


# ───────────────────────── Витрина покупки ──────────────────────────────────


async def test_only_lessonless_plans_are_sold(db: AsyncSession) -> None:
    """Самому продаются только тарифы без занятий.

    Тариф с занятиями — это обещание живого преподавателя в расписании. Продать
    его кнопкой значит пообещать то, что некому выполнить.
    """
    codes = {p["code"] for p in await subs.purchasable_plans(db)}
    assert codes == {"self", "ai"}, f"витрина покупки изменилась: {sorted(codes)}"


async def test_prices_come_from_the_grid(db: AsyncSession) -> None:
    """Цена берётся из тарифной сетки, а не хранится второй раз рядом."""
    by_code = {p["code"]: p for p in await subs.purchasable_plans(db)}
    assert by_code["self"]["price_minor"] == 100_000
    assert by_code["ai"]["price_minor"] == 150_000


# ───────────────────────── Зачисление покупки ───────────────────────────────


async def test_purchase_grants_plan_and_records_money(
    db: AsyncSession, student: int
) -> None:
    """Круг замкнут: тариф выдан, платёж записан, начисление создано."""
    payment_id = f"pay-{uuid.uuid4().hex[:12]}"
    created = await subs.purchase_plan(
        db, student, "ai",
        gateway_payment_id=payment_id, amount_minor=150_000,
        today=date(2026, 8, 5),
    )
    assert created is True
    assert await _plan_of(db, student) == "ai"

    payments = await _payments(db, student)
    assert len(payments) == 1
    assert payments[0][1] == 150_000


async def test_repeated_delivery_is_harmless(db: AsyncSession, student: int) -> None:
    """Повтор доставки не выдаёт тариф дважды и не берёт денег дважды.

    Замок — уникальность платежа: он записывается ПЕРВЫМ, до выдачи прав.
    Обратный порядок выдавал бы права на каждую повторную доставку.
    """
    payment_id = f"pay-{uuid.uuid4().hex[:12]}"
    first = await subs.purchase_plan(
        db, student, "ai", gateway_payment_id=payment_id,
        amount_minor=150_000, today=date(2026, 8, 5),
    )
    second = await subs.purchase_plan(
        db, student, "ai", gateway_payment_id=payment_id,
        amount_minor=150_000, today=date(2026, 8, 5),
    )
    assert (first, second) == (True, False)
    assert len(await _payments(db, student)) == 1

    active = (
        await db.execute(
            text(
                "SELECT count(*) FROM student_subscription "
                " WHERE student_id = :s AND ends_on IS NULL"
            ),
            {"s": student},
        )
    ).scalar()
    assert active == 1, "повтор доставки открыл вторую подписку"


@pytest.mark.parametrize(
    "day,expected_month",
    [
        (5, 8),   # до порога — платит за текущий месяц
        (20, 8),  # ровно порог — ещё текущий
        (21, 9),  # после порога — за следующий, остаток текущего бесплатно
        (28, 9),
    ],
)
async def test_first_month_follows_the_cutoff(
    db: AsyncSession, student: int, day: int, expected_month: int
) -> None:
    """Порог 20-го числа: решение оператора 2026-08-08.

    Человек не должен платить полную цену за три дня ровно в тот момент, когда
    впервые расстаётся с деньгами.
    """
    await subs.purchase_plan(
        db, student, "self",
        gateway_payment_id=f"pay-{uuid.uuid4().hex[:12]}",
        amount_minor=100_000, today=date(2026, 8, day),
    )
    payments = await _payments(db, student)
    assert payments[0][0].month == expected_month, (
        f"покупка {day}-го отнесена не к тому месяцу"
    )


async def test_unknown_plan_is_rejected(db: AsyncSession, student: int) -> None:
    """Тариф, которого нельзя купить, не выдаётся даже по оплаченному платежу.

    Иначе через подделанное поле в уведомлении можно было бы получить «Флагман»
    по цене Self.
    """
    for code in ("base", "test", "alumni", "нет-такого"):
        with pytest.raises(ValueError):
            await subs.purchase_plan(
                db, student, code,
                gateway_payment_id=f"pay-{uuid.uuid4().hex[:12]}",
                amount_minor=100_000,
            )
    assert await _plan_of(db, student) is None
    assert await _payments(db, student) == []


async def test_upgrade_keeps_history(db: AsyncSession, student: int) -> None:
    """Апгрейд закрывает прежнюю строку, а не переписывает её.

    История нужна деньгам: по ней видно, по какой группе считался прошлый месяц.
    """
    await db.execute(
        text(
            "INSERT INTO student_subscription (student_id, plan_id, starts_on) "
            "SELECT :s, id, CURRENT_DATE FROM subscription_plan WHERE code = 'demo'"
        ),
        {"s": student},
    )
    await subs.purchase_plan(
        db, student, "self",
        gateway_payment_id=f"pay-{uuid.uuid4().hex[:12]}",
        amount_minor=100_000, today=date(2026, 8, 5),
    )
    total = (
        await db.execute(
            text("SELECT count(*) FROM student_subscription WHERE student_id = :s"),
            {"s": student},
        )
    ).scalar()
    assert (await _plan_of(db, student), total) == ("self", 2)
