"""tsk-1040 — подсветка ручной оплаты на экране начислений маркетолога.

`ChargeRead.has_manual_payment` — «среди подтверждённого по этому месяцу есть
платёж, отмеченный руками». Признак идёт из `_totals_by_charge` тем же
запросом, что уже считает `paid_minor`/`pending_minor` — проверяем на всех
классах платежей, чтобы бейдж не путал ручную отметку с чеком или шлюзом и не
цеплялся за сброшенную (`reversed`) отметку.
"""

from __future__ import annotations

import pytest

from app.services import payment_service
from tests.test_tsk505_marketer_pricing import _auth, _new_user
from tests.test_tsk511_charges_breaks import _setup, PERIOD
from tests.test_tsk010_payments import _charge_id, _login_as, _marketer_charge, _recalc, _submit
from tests.test_tsk010_staff_payments import _mark

pytestmark = pytest.mark.asyncio


async def test_no_payment_is_not_flagged(db, client):
    """Свежее начисление без единого платежа бейджа не несёт."""
    env = await _setup(db, "flag-none", price=550000)
    await _recalc(db, student_id=env["student_id"])
    _, token = await _new_user(db, role="marketer", name="flag-none-m")

    row = await _marketer_charge(client, token, env["student_id"])
    assert row["has_manual_payment"] is False


async def test_manual_mark_is_flagged(db, client):
    """Ручная отметка персоналом (tsk-544) зажигает бейдж."""
    env = await _setup(db, "flag-manual", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, token = await _new_user(db, role="marketer", name="flag-manual-m")

    await _mark(client, token, env["student_id"], charge_id=charge_id, amount_minor=200000)

    row = await _marketer_charge(client, token, env["student_id"])
    assert row["has_manual_payment"] is True


async def test_confirmed_receipt_is_flagged_too(db, client):
    """Подтверждённый чек ученика пишется method='manual' — тоже подсвечивается.

    Разграничения между «отметил персонал» и «подтвердил чек» в данных нет —
    оба класса физически один и тот же `method='manual'` (см. tsk-1022).
    """
    env = await _setup(db, "flag-receipt", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])
    _, token = await _new_user(db, role="marketer", name="flag-receipt-m")

    resp = await _submit(client, student_token, charge_id=charge_id, amount_minor=200000)
    await client.post(
        f"/api/v1/marketer/payments/{resp.json()['id']}/confirm",
        json={},
        headers=_auth(token),
    )

    row = await _marketer_charge(client, token, env["student_id"])
    assert row["has_manual_payment"] is True


async def test_gateway_payment_is_not_flagged(db, client):
    """Оплата картой (шлюз) — другой метод, бейджа не даёт."""
    env = await _setup(db, "flag-gateway", price=550000)
    await _recalc(db, student_id=env["student_id"])
    _, token = await _new_user(db, role="marketer", name="flag-gateway-m")

    await payment_service.record_gateway_payment(
        db,
        student_id=env["student_id"],
        group_id=env["group_id"],
        period=PERIOD,
        amount_minor=550000,
        gateway="yookassa",
        gateway_payment_id="flag-gateway-txn-1",
        paid_on=None,
    )

    row = await _marketer_charge(client, token, env["student_id"])
    assert row["has_manual_payment"] is False


async def test_pending_receipt_does_not_flag_until_confirmed(db, client):
    """Чек, который ещё не решён, бейдж не зажигает — деньгами он ещё не стал."""
    env = await _setup(db, "flag-pending", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])
    _, token = await _new_user(db, role="marketer", name="flag-pending-m")

    await _submit(client, student_token, charge_id=charge_id, amount_minor=200000)

    row = await _marketer_charge(client, token, env["student_id"])
    assert row["has_manual_payment"] is False


async def test_reversed_manual_payment_clears_the_flag(db, client):
    """Сброшенная отметка (tsk-1022) снимает бейдж — как и снимает деньги."""
    env = await _setup(db, "flag-reversed", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, token = await _new_user(db, role="marketer", name="flag-reversed-m")

    marked = await _mark(
        client, token, env["student_id"], charge_id=charge_id, amount_minor=200000
    )
    payment_id = marked.json()["id"]

    row = await _marketer_charge(client, token, env["student_id"])
    assert row["has_manual_payment"] is True

    reversed_resp = await client.post(
        f"/api/v1/marketer/payments/{payment_id}/reverse",
        json={"note": "перепутал сумму"},
        headers=_auth(token),
    )
    assert reversed_resp.status_code == 200, reversed_resp.text

    row = await _marketer_charge(client, token, env["student_id"])
    assert row["has_manual_payment"] is False
