"""tsk-010 — оплата родителем по гостевой ссылке, без входа.

Контур открыт наружу, поэтому проверяем не удобство, а границы: по ссылке
видно и оплачивается ТОЛЬКО начисление своего ученика, отозванная ссылка не
работает, частота ограничена, а чек уходит без автора (учётной записи у гостя
нет).
"""

from __future__ import annotations

import io

import pytest
from sqlalchemy import text

from app.services import parent_access_link_service, yookassa_service
from app.services.yookassa_service import GatewayPayment
from tests.test_tsk511_charges_breaks import _setup, PERIOD
from tests.test_tsk010_payments import _charge_id, _recalc

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/public/parent-dashboard"


async def _link_for(db, student_id: int) -> str:
    _, raw = await parent_access_link_service.create_link(
        db, student_id=student_id, label="мама", created_by_user_id=None
    )
    return raw


def _receipt() -> dict:
    return {"file": ("cheque.png", io.BytesIO(b"receipt-bytes"), "image/png")}


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    """Ограничитель частоты проверяем отдельным тестом, остальным он мешает."""
    from app.api.v1 import public_parent_payments

    async def never_limited(*a, **kw) -> bool:
        return False

    monkeypatch.setattr(public_parent_payments, "is_rate_limited", never_limited)


async def test_link_shows_only_its_own_student_charges(db, client):
    """По ссылке видны начисления только своего ученика."""
    mine = await _setup(db, "pub-mine", price=550000)
    other = await _setup(db, "pub-other", price=275000)
    await _recalc(db, student_id=mine["student_id"])
    await _recalc(db, student_id=other["student_id"])
    token = await _link_for(db, mine["student_id"])

    resp = await client.get(f"{BASE}/{token}/charges")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert rows, "начисления своего ученика не показаны"
    assert all(r["total_minor"] == 550000 for r in rows), "в выдачу попал чужой ученик"


async def test_cannot_pay_someone_elses_charge_by_link(db, client):
    """Чужое начисление по ссылке не оплатить — даже зная его номер."""
    mine = await _setup(db, "pub-guard-mine", price=550000)
    other = await _setup(db, "pub-guard-other", price=550000)
    await _recalc(db, student_id=other["student_id"])
    other_charge = await _charge_id(db, student_id=other["student_id"])
    token = await _link_for(db, mine["student_id"])

    resp = await client.post(
        f"{BASE}/{token}/payments",
        data={"charge_id": other_charge, "amount_minor": 550000},
        files=_receipt(),
    )
    assert resp.status_code == 404, "по ссылке оплатили чужой месяц"


async def test_revoked_link_pays_nothing(db, client):
    """Отозванная ссылка не платит и не показывает суммы."""
    env = await _setup(db, "pub-revoked", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    token = await _link_for(db, env["student_id"])

    link_id = (
        await db.execute(
            text("SELECT id FROM parent_access_links WHERE student_id = :s ORDER BY id DESC LIMIT 1"),
            {"s": env["student_id"]},
        )
    ).scalar()
    await parent_access_link_service.revoke_link(db, link_id=int(link_id))

    assert (await client.get(f"{BASE}/{token}/charges")).status_code == 404
    resp = await client.post(
        f"{BASE}/{token}/payments",
        data={"charge_id": charge_id, "amount_minor": 550000},
        files=_receipt(),
    )
    assert resp.status_code == 404


async def test_receipt_by_link_lands_in_queue_without_author(db, client):
    """Чек по ссылке попадает в очередь, но без автора: гостя опознать нечем."""
    env = await _setup(db, "pub-receipt", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    token = await _link_for(db, env["student_id"])

    resp = await client.post(
        f"{BASE}/{token}/payments",
        data={"charge_id": charge_id, "amount_minor": 550000, "payer_note": "от мамы"},
        files=_receipt(),
    )
    assert resp.status_code == 201, resp.text

    row = (
        await db.execute(
            text(
                "SELECT status, submitted_by, payer_note, amount_minor "
                "  FROM student_payment WHERE student_id = :s"
            ),
            {"s": env["student_id"]},
        )
    ).one()
    assert row.status == "pending", "чек по ссылке зачёлся без подтверждения"
    assert row.submitted_by is None, "гостю приписали учётную запись"
    assert row.payer_note == "от мамы"
    assert row.amount_minor == 550000


async def test_card_payment_by_link_binds_to_right_charge(db, client, monkeypatch):
    """Оплата картой привязывается к начислению своего ученика и возвращает на ссылку."""
    env = await _setup(db, "pub-card", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    token = await _link_for(db, env["student_id"])

    monkeypatch.setattr(yookassa_service.settings, "yookassa_shop_id", "1426027")
    monkeypatch.setattr(yookassa_service.settings, "yookassa_secret_key", "test_x")
    captured: dict = {}

    async def fake_create(**kwargs) -> GatewayPayment:
        captured.update(kwargs)
        return GatewayPayment(
            id="pub-1", status="pending", amount_minor=kwargs["amount_minor"],
            paid=False, confirmation_url="https://yoomoney.test/p/pub-1",
            test=True, metadata=kwargs["metadata"],
        )

    monkeypatch.setattr(yookassa_service, "create_payment", fake_create)

    resp = await client.post(
        f"{BASE}/{token}/payments/gateway", json={"charge_id": charge_id}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["confirmation_url"].startswith("https://")
    assert captured["metadata"]["charge_id"] == str(charge_id)
    # Возврат ведёт обратно на гостевую страницу: формы входа у родителя нет.
    assert captured["return_url"].endswith(f"/p/{token}")


async def test_rate_limit_closes_the_link(db, client, monkeypatch):
    """Частота ограничена: вечная ссылка не должна быть открытым каналом."""
    from app.api.v1 import public_parent_payments

    env = await _setup(db, "pub-rate", price=550000)
    token = await _link_for(db, env["student_id"])

    async def always_limited(*a, **kw) -> bool:
        return True

    monkeypatch.setattr(public_parent_payments, "is_rate_limited", always_limited)
    resp = await client.get(f"{BASE}/{token}/charges")
    assert resp.status_code == 429


async def test_garbage_token_is_not_a_hint(db, client):
    """Мусорный токен отвечает так же, как несуществующий, — без подсказок."""
    resp = await client.get(f"{BASE}/{'a' * 64}/charges")
    assert resp.status_code == 404
    assert "недействительна" in resp.json()["detail"].lower()
