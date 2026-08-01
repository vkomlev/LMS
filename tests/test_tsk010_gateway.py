"""tsk-010 — оплата картой: идемпотентность и защита денежного контура.

Сам шлюз не дёргаем: подменяем клиент. Проверяем не «сходил ли запрос», а
инварианты денег — повторное уведомление не задваивает платёж, подделанное
уведомление не зачисляется, чужое начисление недоступно, боевой ключ не
включается сам.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from app.services import payment_service, yookassa_service
from app.services.yookassa_service import GatewayPayment
from tests.test_tsk505_marketer_pricing import _auth, _new_user
from tests.test_tsk511_charges_breaks import _setup, PERIOD
from tests.test_tsk010_payments import _charge_id, _login_as, _recalc

pytestmark = pytest.mark.asyncio


def _gateway_payment(
    *, payment_id: str, charge_id: int, student_id: int, amount_minor: int = 550000,
    status: str = "succeeded", paid: bool = True, test: bool = True,
) -> GatewayPayment:
    return GatewayPayment(
        id=payment_id,
        status=status,
        amount_minor=amount_minor,
        paid=paid,
        confirmation_url=f"https://yoomoney.test/checkout/{payment_id}",
        test=test,
        metadata={"charge_id": str(charge_id), "student_id": str(student_id)},
    )


@pytest.fixture
def gateway_on(monkeypatch):
    """Включить оплату картой с тестовым ключом."""
    for module in (yookassa_service, ):
        monkeypatch.setattr(module.settings, "yookassa_shop_id", "1426025")
        monkeypatch.setattr(module.settings, "yookassa_secret_key", "test_secret")
        monkeypatch.setattr(module.settings, "yookassa_allow_live", False)
    return True


async def _webhook(client, payment_id: str, *, charge_id: int = 0) -> object:
    """Уведомление от шлюза. Тело намеренно скудное — ему всё равно не верят."""
    body = {"type": "notification", "event": "payment.succeeded", "object": {"id": payment_id}}
    if charge_id:
        # Подделка: в теле свой charge_id. Сервер обязан взять его из ответа шлюза.
        body["object"]["metadata"] = {"charge_id": str(charge_id)}
    return await client.post("/api/v1/payments/yookassa/webhook", json=body)


async def test_repeat_notification_does_not_double_money(db, client, monkeypatch, gateway_on):
    """Повторная доставка уведомления — обычное дело, а не вторые деньги."""
    env = await _setup(db, "gw-idem", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])

    payment = _gateway_payment(payment_id="2f0d1a-test-1", charge_id=charge_id,
                               student_id=env["student_id"])

    async def fake_fetch(payment_id: str) -> GatewayPayment:
        assert payment_id == "2f0d1a-test-1"
        return payment

    monkeypatch.setattr(yookassa_service, "fetch_payment", fake_fetch)

    first = await _webhook(client, "2f0d1a-test-1")
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "recorded"

    second = await _webhook(client, "2f0d1a-test-1")
    assert second.status_code == 200
    assert second.json()["status"] == "already_recorded"

    rows = (
        await db.execute(
            text("SELECT count(*) AS n, sum(amount_minor) AS total FROM student_payment "
                 "WHERE student_id = :s"),
            {"s": env["student_id"]},
        )
    ).one()
    assert rows.n == 1, "повторное уведомление завело второй платёж"
    assert rows.total == 550000


async def test_notification_body_cannot_redirect_money(db, client, monkeypatch, gateway_on):
    """Метаданные берутся у шлюза, а не из тела: чужой месяц не оплатить подделкой."""
    victim = await _setup(db, "gw-victim", price=550000)
    attacker = await _setup(db, "gw-attacker", price=550000)
    await _recalc(db, student_id=victim["student_id"])
    await _recalc(db, student_id=attacker["student_id"])
    victim_charge = await _charge_id(db, student_id=victim["student_id"])
    attacker_charge = await _charge_id(db, student_id=attacker["student_id"])

    # Шлюз говорит: платёж на начисление ЗЛОУМЫШЛЕННИКА (он и правда его оплатил).
    async def fake_fetch(payment_id: str) -> GatewayPayment:
        return _gateway_payment(payment_id=payment_id, charge_id=attacker_charge,
                                student_id=attacker["student_id"])

    monkeypatch.setattr(yookassa_service, "fetch_payment", fake_fetch)

    # А в теле уведомления подсовывает чужое начисление.
    resp = await _webhook(client, "spoof-1", charge_id=victim_charge)
    assert resp.status_code == 200

    victim_paid = (
        await db.execute(
            text("SELECT count(*) AS n FROM student_payment WHERE student_id = :s"),
            {"s": victim["student_id"]},
        )
    ).one()
    assert victim_paid.n == 0, "деньги ушли на чужое начисление по телу уведомления"


async def test_unpaid_payment_is_not_credited(db, client, monkeypatch, gateway_on):
    """Платёж не в статусе «оплачен» не зачисляется."""
    env = await _setup(db, "gw-pending", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])

    async def fake_fetch(payment_id: str) -> GatewayPayment:
        return _gateway_payment(payment_id=payment_id, charge_id=charge_id,
                                student_id=env["student_id"],
                                status="pending", paid=False)

    monkeypatch.setattr(yookassa_service, "fetch_payment", fake_fetch)

    resp = await _webhook(client, "not-paid-1")
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"

    rows = (
        await db.execute(
            text("SELECT count(*) AS n FROM student_payment WHERE student_id = :s"),
            {"s": env["student_id"]},
        )
    ).one()
    assert rows.n == 0


async def test_unreachable_gateway_asks_for_redelivery(db, client, monkeypatch, gateway_on):
    """Если шлюз недоступен — отвечаем ошибкой, чтобы уведомление доставили снова.

    Молчаливое «ок» здесь означало бы потерянные деньги: списание уже прошло.
    """
    async def fake_fetch(payment_id: str) -> GatewayPayment:
        raise yookassa_service.GatewayError("сеть недоступна")

    monkeypatch.setattr(yookassa_service, "fetch_payment", fake_fetch)

    resp = await _webhook(client, "net-down-1")
    assert resp.status_code == 502


async def test_live_key_without_permission_disables_card_payment(monkeypatch):
    """Боевой ключ сам по себе способ не включает — нужен явный разрешающий флаг."""
    monkeypatch.setattr(yookassa_service.settings, "yookassa_shop_id", "1426025")
    monkeypatch.setattr(yookassa_service.settings, "yookassa_secret_key", "live_secret")
    monkeypatch.setattr(yookassa_service.settings, "yookassa_allow_live", False)
    assert yookassa_service.is_test_mode() is False
    assert yookassa_service.is_enabled() is False

    monkeypatch.setattr(yookassa_service.settings, "yookassa_allow_live", True)
    assert yookassa_service.is_enabled() is True


async def test_card_payment_is_offered_only_when_configured(db, client, monkeypatch):
    """Без настроек кабинет честно говорит, что способ недоступен."""
    monkeypatch.setattr(yookassa_service.settings, "yookassa_shop_id", "")
    monkeypatch.setattr(yookassa_service.settings, "yookassa_secret_key", "")
    env = await _setup(db, "gw-off", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, token = await _login_as(db, env["student_id"])

    status_resp = await client.get("/api/v1/me/payments/gateway-status", headers=_auth(token))
    assert status_resp.json()["enabled"] is False

    resp = await client.post(
        "/api/v1/me/payments/gateway", json={"charge_id": charge_id}, headers=_auth(token)
    )
    assert resp.status_code == 503


async def test_cannot_pay_someone_elses_charge_by_card(db, client, monkeypatch, gateway_on):
    """Чужое начисление картой не оплатить — как и чеком."""
    mine = await _setup(db, "gw-mine", price=550000)
    other = await _setup(db, "gw-other", price=550000)
    await _recalc(db, student_id=other["student_id"])
    other_charge = await _charge_id(db, student_id=other["student_id"])
    _, token = await _login_as(db, mine["student_id"])

    resp = await client.post(
        "/api/v1/me/payments/gateway", json={"charge_id": other_charge}, headers=_auth(token)
    )
    assert resp.status_code == 404


async def test_amount_over_remainder_is_refused(db, client, monkeypatch, gateway_on):
    """Через шлюз не принимаем больше остатка: вернуть переплату сложнее, чем не взять."""
    env = await _setup(db, "gw-over", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, token = await _login_as(db, env["student_id"])

    resp = await client.post(
        "/api/v1/me/payments/gateway",
        json={"charge_id": charge_id, "amount_minor": 9_000_000},
        headers=_auth(token),
    )
    assert resp.status_code == 422


async def test_payment_start_returns_link_and_test_flag(db, client, monkeypatch, gateway_on):
    """Кабинет получает ссылку на оплату и честный признак тестового режима."""
    env = await _setup(db, "gw-start", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, token = await _login_as(db, env["student_id"])

    captured: dict = {}

    async def fake_create(**kwargs) -> GatewayPayment:
        captured.update(kwargs)
        return _gateway_payment(payment_id="start-1", charge_id=charge_id,
                                student_id=env["student_id"])

    monkeypatch.setattr(yookassa_service, "create_payment", fake_create)

    resp = await client.post(
        "/api/v1/me/payments/gateway", json={"charge_id": charge_id}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["confirmation_url"].startswith("https://")
    assert data["test_mode"] is True
    # Сумма по умолчанию — весь остаток месяца, а не сумма из запроса.
    assert captured["amount_minor"] == 550000
    # Начисление привязано метаданными: по ним webhook найдёт, что оплачено.
    assert captured["metadata"]["charge_id"] == str(charge_id)


async def test_reconcile_picks_up_lost_payment(db, client, monkeypatch, gateway_on):
    """Сверка добирает платёж, о котором уведомление не дошло.

    Это не гипотетический случай: на живой проверке уведомления не были
    настроены в кабинете шлюза, деньги ушли, а в кабинете висел долг.
    """
    env = await _setup(db, "gw-recon", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])

    lost = _gateway_payment(payment_id="lost-1", charge_id=charge_id,
                            student_id=env["student_id"])
    orphan = _gateway_payment(payment_id="orphan-1", charge_id=10**7,
                              student_id=env["student_id"])

    async def fake_list(**kwargs) -> list[GatewayPayment]:
        return [lost, orphan]

    monkeypatch.setattr(yookassa_service, "list_succeeded", fake_list)

    today = date.today().isoformat()
    first = await client.post(
        f"/api/v1/marketer/payments/reconcile?date_from={today}&date_to={today}",
        headers=_auth(env["token"]),
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["added"] == 1
    # Платёж, которому не нашлось начисления, назван поимённо — молчать о
    # деньгах, которые некуда положить, нельзя.
    assert body["without_charge"] == ["orphan-1"]

    # Повторная сверка ничего не задваивает.
    second = await client.post(
        f"/api/v1/marketer/payments/reconcile?date_from={today}&date_to={today}",
        headers=_auth(env["token"]),
    )
    assert second.json()["added"] == 0

    rows = (
        await db.execute(
            text("SELECT count(*) AS n, sum(amount_minor) AS total FROM student_payment "
                 "WHERE student_id = :s"),
            {"s": env["student_id"]},
        )
    ).one()
    assert rows.n == 1
    assert rows.total == 550000


async def test_reconcile_is_closed_for_students(db, client, gateway_on):
    """Сверка — инструмент маркетолога, ученику она недоступна."""
    env = await _setup(db, "gw-recon-gate", price=550000)
    _, token = await _login_as(db, env["student_id"])
    today = date.today().isoformat()

    resp = await client.post(
        f"/api/v1/marketer/payments/reconcile?date_from={today}&date_to={today}",
        headers=_auth(token),
    )
    assert resp.status_code == 403


def test_amount_conversion_has_no_float_drift():
    """Рубли шлюза переводятся в копейки без плавающей точки."""
    parsed = yookassa_service._parse(
        {"id": "x", "status": "succeeded", "paid": True, "test": True,
         "amount": {"value": "5500.00", "currency": "RUB"}}
    )
    assert parsed.amount_minor == 550000

    kopecks = yookassa_service._parse(
        {"id": "x", "status": "succeeded", "paid": True, "test": True,
         "amount": {"value": "2750.35", "currency": "RUB"}}
    )
    assert kopecks.amount_minor == 275035
