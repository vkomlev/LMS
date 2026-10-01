"""tsk-1194 — исход «принят уход без оплаты» у долга ушедшего ученика.

Политика школы: ушёл и не заплатил — долг уходит из итогов, долгов, напоминаний
и блокировки, но строка и сумма остаются в истории. Пришедшая позже оплата
переводит строку в оплаченные без снятия отметки.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from app.services import (
    charge_service,
    charge_writeoff_service,
    payment_access_service,
    payment_reminder_service,
    payment_service,
)
from tests.test_tsk010_payments import _recalc
from tests.test_tsk505_marketer_pricing import _auth
from tests.test_tsk511_charges_breaks import PERIOD, _setup
from tests.test_tsk805_alumni_debts import _close_month, _make_alumni

pytestmark = pytest.mark.asyncio

OVERDUE_DAY = payment_service.due_date_for(PERIOD) + timedelta(days=30)


async def _alumni_with_debt(db, tag: str) -> dict:
    env = await _setup(db, tag, price=550000)
    await _recalc(db, student_id=env["student_id"])
    await _close_month(db, env["student_id"])
    await _make_alumni(db, env["student_id"])
    env["charge_id"] = (
        await db.execute(
            text("SELECT id FROM student_monthly_charge WHERE student_id = :s AND period = :p"),
            {"s": env["student_id"], "p": PERIOD},
        )
    ).scalar_one()
    return env


async def _row(client, env) -> dict:
    resp = await client.get(
        f"/api/v1/marketer/charges?period={PERIOD.isoformat()}", headers=_auth(env["token"])
    )
    assert resp.status_code == 200, resp.text
    return next(c for c in resp.json() if c["student_id"] == env["student_id"])


async def test_backfill_moves_alumni_debt_out_of_debts(db, client):
    """Разовый проход: долг ушедшего уходит из списка, строка остаётся с суммой."""
    env = await _alumni_with_debt(db, "t1194-fill")

    plan = await charge_writeoff_service.write_off_alumni_debts(db)
    assert any(p["student_id"] == env["student_id"] for p in plan)
    still = await payment_reminder_service.list_alumni_debts(db, today=OVERDUE_DAY)
    assert any(d.student_id == env["student_id"] for d in still), "без apply — только план"

    await charge_writeoff_service.write_off_alumni_debts(db, apply=True)
    debts = await payment_reminder_service.list_alumni_debts(db, today=OVERDUE_DAY)
    assert not any(d.student_id == env["student_id"] for d in debts)

    row = await _row(client, env)
    assert row["total_minor"] == 550000, "сумма остаётся в истории"
    assert row["written_off_minor"] == 550000
    assert row["due_minor"] == 0
    assert row["is_overdue"] is False


async def test_written_off_does_not_block_or_nag(db):
    """Ни блокировки, ни плашки «скоро срок» по принятому уходу."""
    env = await _setup(db, "t1194-block", price=550000)
    await _recalc(db, student_id=env["student_id"])
    await charge_writeoff_service.write_off_lines(
        db, student_id=env["student_id"], lines=[(env["group_id"], PERIOD)], written_off_by=None
    )
    await db.commit()
    assert await payment_access_service.blocking_debt(
        db, env["student_id"], today=OVERDUE_DAY
    ) is None
    assert await payment_service.due_soon_notice(
        db, env["student_id"], today=OVERDUE_DAY
    ) is None
    overdue = await payment_reminder_service.list_overdue(db, today=OVERDUE_DAY)
    assert not any(d.student_id == env["student_id"] for d in overdue)


async def test_late_payment_turns_row_into_paid(db, client):
    """Заплатили позже — строка оплачена, отметку снимать не нужно."""
    env = await _alumni_with_debt(db, "t1194-late")
    resp = await client.post(
        f"/api/v1/marketer/charges/{env['charge_id']}/write-off", headers=_auth(env["token"])
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["written_off_minor"] == 550000

    await db.execute(
        text(
            "INSERT INTO student_payment "
            "(student_id, group_id, period, amount_minor, method, status, reviewed_at, purpose) "
            "VALUES (:s, :g, :p, 550000, 'manual', 'confirmed', now(), 'monthly')"
        ),
        {"s": env["student_id"], "g": env["group_id"], "p": PERIOD},
    )
    await db.commit()
    row = await _row(client, env)
    assert row["written_off_minor"] == 0
    assert row["paid_minor"] == 550000
    assert row["due_minor"] == 0


async def test_restore_returns_row_to_debts(db, client):
    """«Вернуть в долги» снимает исход: долг снова в списке ушедших."""
    env = await _alumni_with_debt(db, "t1194-back")
    await client.post(
        f"/api/v1/marketer/charges/{env['charge_id']}/write-off", headers=_auth(env["token"])
    )
    resp = await client.delete(
        f"/api/v1/marketer/charges/{env['charge_id']}/write-off", headers=_auth(env["token"])
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["written_off_minor"] == 0
    assert resp.json()["due_minor"] == 550000
    debts = await payment_reminder_service.list_alumni_debts(db, today=OVERDUE_DAY)
    assert any(d.student_id == env["student_id"] for d in debts)


async def test_write_off_unknown_charge_is_404(db, client):
    env = await _setup(db, "t1194-404", price=550000)
    resp = await client.post(
        "/api/v1/marketer/charges/999999999/write-off", headers=_auth(env["token"])
    )
    assert resp.status_code == 404


async def test_dashboard_charges_exclude_written_off(db):
    """Плитка начислений не считает принятый уход без оплаты."""
    from app.services import marketer_dashboard_service

    env = await _alumni_with_debt(db, "t1194-tile")
    before = await marketer_dashboard_service._charges_total_for_month(db, PERIOD)
    await charge_writeoff_service.write_off_charge(
        db, charge_id=env["charge_id"], written_off_by=None
    )
    after = await marketer_dashboard_service._charges_total_for_month(db, PERIOD)
    assert before - after == 550000
    assert charge_service.month_start(PERIOD) == PERIOD
