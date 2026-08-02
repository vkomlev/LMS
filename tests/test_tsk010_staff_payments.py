"""tsk-010 — ручная отметка оплаты в карточке ученика.

Способ существует ровно потому, что чека нет и не будет: месяц оплатили до
внедрения системы либо человек не разобрался с кабинетом. Значит проверяем, что
такой платёж сразу считается деньгами, помнит автора и причину, и что отметить
его может не кто угодно.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.test_tsk505_marketer_pricing import _auth, _new_user
from tests.test_tsk511_charges_breaks import _setup, PERIOD
from tests.test_tsk010_payments import _charge_id, _login_as, _recalc

pytestmark = pytest.mark.asyncio


async def _mark(client, token: str, student_id: int, **body):
    return await client.post(
        f"/api/v1/staff/students/{student_id}/payments",
        json={"note": "оплачено переводом до внедрения", **body},
        headers=_auth(token),
    )


async def test_manual_mark_closes_the_debt(db, client):
    """Отмеченная оплата сразу гасит долг — подтверждать нечего."""
    env = await _setup(db, "staff-pay", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, methodist_token = await _new_user(db, role="methodist", name="staff-pay-m")

    resp = await _mark(
        client, methodist_token, env["student_id"], charge_id=charge_id, amount_minor=550000
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "confirmed"

    row = (
        await db.execute(
            text(
                "SELECT status, method, receipt_file, review_note, reviewed_by, reviewed_at "
                "  FROM student_payment WHERE student_id = :s"
            ),
            {"s": env["student_id"]},
        )
    ).one()
    assert row.status == "confirmed"
    assert row.method == "manual"
    assert row.receipt_file is None, "чека быть не должно — его и не прикладывали"
    assert row.review_note == "оплачено переводом до внедрения"
    assert row.reviewed_by is not None, "непонятно, кто отметил оплату"
    assert row.reviewed_at is not None

    # Долг закрыт: у ученика в кабинете остатка больше нет.
    _, student_token = await _login_as(db, env["student_id"])
    charges = await client.get("/api/v1/me/charges", headers=_auth(student_token))
    assert charges.json()[0]["due_minor"] == 0


async def test_note_is_required(db, client):
    """Без причины отметить нельзя: иначе деньги появятся без объяснения."""
    env = await _setup(db, "staff-note", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, token = await _new_user(db, role="methodist", name="staff-note-m")

    resp = await client.post(
        f"/api/v1/staff/students/{env['student_id']}/payments",
        json={"charge_id": charge_id, "amount_minor": 550000, "note": ""},
        headers=_auth(token),
    )
    assert resp.status_code == 422


async def test_cannot_mark_someone_elses_charge(db, client):
    """Начисление соседа не отметить, даже перепутав номер."""
    mine = await _setup(db, "staff-mine", price=550000)
    other = await _setup(db, "staff-other", price=550000)
    await _recalc(db, student_id=other["student_id"])
    other_charge = await _charge_id(db, student_id=other["student_id"])
    _, token = await _new_user(db, role="methodist", name="staff-guard-m")

    resp = await _mark(
        client, token, mine["student_id"], charge_id=other_charge, amount_minor=550000
    )
    assert resp.status_code == 404

    left = (
        await db.execute(
            text("SELECT count(*) AS n FROM student_payment WHERE student_id = :s"),
            {"s": other["student_id"]},
        )
    ).one()
    assert left.n == 0, "оплата ушла чужому ученику"


async def test_student_cannot_mark_own_payment(db, client):
    """Ученик не может объявить свой месяц оплаченным."""
    env = await _setup(db, "staff-student", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    resp = await _mark(
        client, student_token, env["student_id"], charge_id=charge_id, amount_minor=550000
    )
    assert resp.status_code == 403


async def test_partial_manual_mark_leaves_the_rest(db, client):
    """Частичная отметка гасит только свою часть — остаток остаётся долгом."""
    env = await _setup(db, "staff-partial", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, token = await _new_user(db, role="methodist", name="staff-partial-m")

    resp = await _mark(
        client, token, env["student_id"], charge_id=charge_id, amount_minor=200000
    )
    assert resp.status_code == 201

    rows = await client.get(
        f"/api/v1/staff/students/{env['student_id']}/charges", headers=_auth(token)
    )
    charge = rows.json()[0]
    assert charge["paid_minor"] == 200000
    assert charge["due_minor"] == 350000


async def test_future_payment_date_is_refused(db, client):
    """Дата платежа в будущем — отказ, как и при загрузке чека."""
    from datetime import date, timedelta

    env = await _setup(db, "staff-future", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, token = await _new_user(db, role="methodist", name="staff-future-m")

    resp = await _mark(
        client,
        token,
        env["student_id"],
        charge_id=charge_id,
        amount_minor=550000,
        paid_on=(date.today() + timedelta(days=1)).isoformat(),
    )
    assert resp.status_code == 422
