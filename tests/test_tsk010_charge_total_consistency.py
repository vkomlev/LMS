"""tsk-010 — формула итога месяца сведена в одно место.

До этой правки `COALESCE(manual, calculated) + adjustments` дублировалась как
raw SQL в `payment_service.list_payments`, `payment_reminder_service.list_overdue`,
`payment_access_service.has_blocking_debt` и как Python-код в
`charge_service.list_charges` / `payment_service.list_student_charges`.
Разъехавшись, три места денежного контура (список платежей, напоминание о
просрочке, проверка блокировки) могли бы показать разное число по одному и
тому же месяцу. Тест фиксирует, что для (student_id, group_id, period) с
непустыми manual_minor, calculated_minor и adjustments все они видят ОДНО и
то же число — не проверяет каждый сервис в изоляции.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from app.services import (
    charge_service,
    payment_access_service,
    payment_reminder_service,
    payment_service,
)
from tests.test_tsk010_payments import _charge_id, _recalc
from tests.test_tsk511_charges_breaks import PERIOD, _setup

pytestmark = pytest.mark.asyncio


async def _add_adjustment(
    db, *, student_id: int, group_id: int, period, amount_minor: int
) -> None:
    """Ручная поправка месяца — тот же путь, каким `_carry_forward` заводит перенос."""
    await db.execute(
        text(
            "INSERT INTO charge_adjustment "
            "       (student_id, group_id, period, amount_minor, reason, source) "
            "VALUES (:s, :g, :p, :amt, 'tsk-010: проверка сведения формулы', 'manual')"
        ),
        {"s": student_id, "g": group_id, "p": period, "amt": amount_minor},
    )
    await db.commit()


async def test_total_matches_across_all_three_consumers(db):
    """Ручная цена + поправка дают одно число в списке начислений, очереди
    платежей, напоминании о просрочке и проверке блокировки."""
    env = await _setup(db, "total-consistency", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])

    # Ручная цена месяца отличается от расчётной (550000) — формула обязана
    # взять её, а не расчёт.
    assert await charge_service.set_manual_amount(
        db, charge_id=charge_id, amount_minor=600000
    )
    await _add_adjustment(
        db,
        student_id=env["student_id"],
        group_id=env["group_id"],
        period=PERIOD,
        amount_minor=15000,
    )
    expected_total = 600000 + 15000  # ручная сумма + поправка, БЕЗ calculated_minor

    # 1. Список начислений маркетолога.
    charges = await charge_service.list_charges(db, period=PERIOD)
    charge_row = next(c for c in charges if c["student_id"] == env["student_id"])
    assert charge_row["calculated_minor"] == 550000, "расчёт остался прежним — не он должен победить"
    assert charge_row["manual_minor"] == 600000
    assert charge_row["adjustments_minor"] == 15000
    assert charge_row["total_minor"] == expected_total

    # 2. Очередь/история платежей маркетолога — тот же charge_total_minor,
    # хотя раньше считался отдельным raw SQL выражением.
    await payment_service.record_staff_payment(
        db,
        student_id=env["student_id"],
        group_id=env["group_id"],
        period=PERIOD,
        amount_minor=100000,
        paid_on=None,
        note="tsk-010: частичная оплата для проверки формулы",
        recorded_by=env["student_id"],
    )
    payments = await payment_service.list_payments(db, student_id=env["student_id"])
    assert payments, "платёж должен попасть в выборку"
    assert payments[0]["charge_total_minor"] == expected_total, (
        "очередь платежей обязана видеть ту же сумму месяца, что и список начислений"
    )
    assert payments[0]["charge_due_minor"] == expected_total - 100000

    # 3. Напоминание о просрочке — due_minor обязан совпасть с тем же остатком.
    overdue_day = payment_service.due_date_for(PERIOD) + timedelta(days=1)
    debtors = await payment_reminder_service.list_overdue(db, today=overdue_day)
    debtor = next(d for d in debtors if d.student_id == env["student_id"])
    assert debtor.due_minor == expected_total - 100000, (
        "напоминание обязано считать долг от той же суммы месяца"
    )

    # 4. Проверка блокировки — граница закрытия занятий должна лежать РОВНО на
    # том же total_minor, что видели предыдущие два места: не хватает 1 копейки
    # до expected_total — блок стоит; копейка доплачена — блок снят.
    block_day = payment_service.block_date_for(PERIOD)
    assert await payment_access_service.has_blocking_debt(
        db, env["student_id"], today=block_day
    ), "остаток не погашен — блокировка обязана быть"

    remaining = expected_total - 100000
    await payment_service.record_staff_payment(
        db,
        student_id=env["student_id"],
        group_id=env["group_id"],
        period=PERIOD,
        amount_minor=remaining - 1,
        paid_on=None,
        note="tsk-010: почти всё, не хватает 1 копейки",
        recorded_by=env["student_id"],
    )
    assert await payment_access_service.has_blocking_debt(
        db, env["student_id"], today=block_day
    ), "не хватает ровно 1 копейки до той же суммы — блокировка должна остаться"

    await payment_service.record_staff_payment(
        db,
        student_id=env["student_id"],
        group_id=env["group_id"],
        period=PERIOD,
        amount_minor=1,
        paid_on=None,
        note="tsk-010: последняя копейка",
        recorded_by=env["student_id"],
    )
    assert not await payment_access_service.has_blocking_debt(
        db, env["student_id"], today=block_day
    ), "остаток погашен ровно до expected_total — блокировка обязана сняться"
