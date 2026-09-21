"""tsk-1022 — сброс ошибочной ручной отметки оплаты.

Отметить платёж руками можно (`record_staff_payment`, сразу `confirmed`) —
раньше обратного пути не было вовсе: ошибся суммой или учеником — правь БД
руками. Проверяем сам откат (долг возвращается), причину (обязательна) и то,
что кнопка физически не может тронуть чужой класс платежей: платёж со шлюза,
ещё не решённый чек или уже сброшенный/отклонённый платёж.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services import payment_service
from tests.test_tsk505_marketer_pricing import _auth, _new_user
from tests.test_tsk511_charges_breaks import _setup, PERIOD
from tests.test_tsk010_payments import _charge_id, _login_as, _marketer_charge, _recalc, _submit
from tests.test_tsk010_staff_payments import _mark

pytestmark = pytest.mark.asyncio


async def _reverse(client, token: str, payment_id: int, **body):
    return await client.post(
        f"/api/v1/marketer/payments/{payment_id}/reverse",
        json={"note": "маркетолог перепутал ученика", **body},
        headers=_auth(token),
    )


async def test_reverse_gives_back_the_debt(db, client):
    """Сброс возвращает долг ровно к состоянию до ошибочной отметки."""
    env = await _setup(db, "reverse-debt", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, token = await _new_user(db, role="marketer", name="reverse-debt-m")

    marked = await _mark(client, token, env["student_id"], charge_id=charge_id, amount_minor=550000)
    assert marked.status_code == 201, marked.text
    payment_id = marked.json()["id"]

    row = await _marketer_charge(client, token, env["student_id"])
    assert row["due_minor"] == 0, "отметка обязана закрыть долг"

    resp = await _reverse(client, token, payment_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "reversed"

    row = await _marketer_charge(client, token, env["student_id"])
    assert row["paid_minor"] == 0
    assert row["due_minor"] == row["total_minor"], "долг обязан вернуться целиком"

    stored = (
        await db.execute(
            text(
                "SELECT status, review_note, reviewed_by "
                "  FROM student_payment WHERE id = :id"
            ),
            {"id": payment_id},
        )
    ).one()
    assert stored.status == "reversed"
    assert stored.review_note == "маркетолог перепутал ученика"
    assert stored.reviewed_by is not None


async def test_reverse_requires_a_reason(db, client):
    """Без причины сброс не проходит — иначе деньги пропадают без объяснения."""
    env = await _setup(db, "reverse-note", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, token = await _new_user(db, role="marketer", name="reverse-note-m")

    marked = await _mark(client, token, env["student_id"], charge_id=charge_id, amount_minor=550000)
    payment_id = marked.json()["id"]

    resp = await client.post(
        f"/api/v1/marketer/payments/{payment_id}/reverse",
        json={"note": ""},
        headers=_auth(token),
    )
    assert resp.status_code == 422

    stored = (
        await db.execute(
            text("SELECT status FROM student_payment WHERE id = :id"), {"id": payment_id}
        )
    ).one()
    assert stored.status == "confirmed", "без причины статус не должен был измениться"


async def test_reverse_is_made_once(db, client):
    """Повторный сброс того же платежа не проходит — решение принимается один раз."""
    env = await _setup(db, "reverse-once", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, token = await _new_user(db, role="marketer", name="reverse-once-m")

    marked = await _mark(client, token, env["student_id"], charge_id=charge_id, amount_minor=550000)
    payment_id = marked.json()["id"]

    first = await _reverse(client, token, payment_id)
    assert first.status_code == 200, first.text

    second = await _reverse(client, token, payment_id)
    assert second.status_code == 404, "сброшенный платёж нельзя сбросить второй раз"


async def test_reverse_does_not_touch_gateway_payment(db, client):
    """Платёж со шлюза кнопка не берёт — гейт по `method`, не только по статусу."""
    env = await _setup(db, "reverse-gateway", price=550000)
    await _recalc(db, student_id=env["student_id"])
    _, token = await _new_user(db, role="marketer", name="reverse-gateway-m")

    added = await payment_service.record_gateway_payment(
        db,
        student_id=env["student_id"],
        group_id=env["group_id"],
        period=PERIOD,
        amount_minor=550000,
        gateway="yookassa",
        gateway_payment_id="reverse-gateway-txn-1",
        paid_on=None,
    )
    assert added is True
    payment_id = (
        await db.execute(
            text("SELECT id FROM student_payment WHERE student_id = :s"),
            {"s": env["student_id"]},
        )
    ).one().id

    resp = await _reverse(client, token, payment_id)
    assert resp.status_code == 404, "платёж со шлюза должен быть недосягаем для сброса"

    stored = (
        await db.execute(
            text("SELECT status FROM student_payment WHERE id = :id"), {"id": payment_id}
        )
    ).one()
    assert stored.status == "confirmed"


async def test_reverse_does_not_touch_pending_receipt(db, client):
    """Чек, который ещё не решён, сбрасывать нечем — сначала решение."""
    env = await _setup(db, "reverse-pending", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])
    _, token = await _new_user(db, role="marketer", name="reverse-pending-m")

    resp = await _submit(client, student_token, charge_id=charge_id, amount_minor=550000)
    assert resp.status_code == 201, resp.text
    payment_id = resp.json()["id"]

    reversed_resp = await _reverse(client, token, payment_id)
    assert reversed_resp.status_code == 404


async def test_reverse_does_not_touch_rejected_payment(db, client):
    """Уже отклонённый чек — другой класс решения, сброс его не берёт."""
    env = await _setup(db, "reverse-rejected", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])
    _, token = await _new_user(db, role="marketer", name="reverse-rejected-m")

    resp = await _submit(client, student_token, charge_id=charge_id, amount_minor=550000)
    payment_id = resp.json()["id"]
    rejected = await client.post(
        f"/api/v1/marketer/payments/{payment_id}/reject",
        json={"note": "чек не читается"},
        headers=_auth(token),
    )
    assert rejected.status_code == 200, rejected.text

    reversed_resp = await _reverse(client, token, payment_id)
    assert reversed_resp.status_code == 404


async def test_only_marketer_reaches_reverse(db, client):
    """Ученик не может сбросить свой же собственный платёж."""
    env = await _setup(db, "reverse-gate", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, token = await _new_user(db, role="marketer", name="reverse-gate-m")
    _, student_token = await _login_as(db, env["student_id"])

    marked = await _mark(client, token, env["student_id"], charge_id=charge_id, amount_minor=550000)
    payment_id = marked.json()["id"]

    resp = await _reverse(client, student_token, payment_id)
    assert resp.status_code == 403
