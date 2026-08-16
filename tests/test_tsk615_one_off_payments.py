"""tsk-615 — деньги за разовую покупку видны в учёте наравне с месяцами.

16.08.2026 прошла первая живая покупка пакета обращений к наставнику (500 ₽).
Пакет зачислился, деньги — нет: `student_payment` умела только платежи за
месяц. Здесь проверяется не «записалась ли строка», а то, ради чего задача
делалась: сумма в LMS сходится с суммой в шлюзе, и при этом разовая покупка
не притворяется оплатой месяца.

Проверяются границы, а не счастливый путь:
 * разовая покупка НЕ гасит долг за месяц (иначе пакет закрывал бы обучение);
 * повтор доставки уведомления не заводит вторые деньги;
 * покупка видна и ученику, и маркетологу, и в выгрузке для сверки;
 * сверка со шлюзом дозакрывает покупку, если уведомление не дошло, — раньше
   такой платёж объявлялся «без начисления», то есть необъяснимым;
 * схема не даёт перепутать назначение с месяцем ни в одну сторону.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.services import payment_service, subscription_service, yookassa_service
from app.services.yookassa_service import GatewayPayment
from tests.test_tsk505_marketer_pricing import _auth
from tests.test_tsk511_charges_breaks import _setup, PERIOD
from tests.test_tsk010_payments import _charge_id, _login_as, _recalc

pytestmark = pytest.mark.asyncio

#: Цена пакета в живом прогоне 16.08.2026 — 500 ₽.
PACKAGE_MINOR = 50000
PACKAGE_UNITS = 40


def _package_payment(
    *, payment_id: str, student_id: int, units: int = PACKAGE_UNITS,
    amount_minor: int = PACKAGE_MINOR, paid: bool = True,
) -> GatewayPayment:
    """Ответ шлюза о покупке пакета — ровно тот, что пришёл в живом прогоне."""
    return GatewayPayment(
        id=payment_id,
        status="succeeded",
        amount_minor=amount_minor,
        paid=paid,
        confirmation_url=f"https://yoomoney.test/checkout/{payment_id}",
        test=True,
        metadata={
            "purpose": payment_service.PURPOSE_AI_PACKAGE,
            "student_id": str(student_id),
            "units": str(units),
        },
    )


async def _webhook(client, payment_id: str):
    body = {"type": "notification", "event": "payment.succeeded",
            "object": {"id": payment_id}}
    return await client.post("/api/v1/payments/yookassa/webhook", json=body)


async def _payment_rows(db, student_id: int) -> list[dict]:
    rows = (
        await db.execute(
            text(
                "SELECT purpose, group_id, period, amount_minor, status, method, "
                "       gateway_payment_id "
                "  FROM student_payment WHERE student_id = :s ORDER BY id"
            ),
            {"s": student_id},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


@pytest.fixture
def gateway_on(monkeypatch):
    """Включить оплату картой с тестовым ключом."""
    monkeypatch.setattr(yookassa_service.settings, "yookassa_shop_id", "1426025")
    monkeypatch.setattr(yookassa_service.settings, "yookassa_secret_key", "test_secret")
    monkeypatch.setattr(yookassa_service.settings, "yookassa_allow_live", False)
    return True


async def test_package_purchase_lands_in_payments(db, client, monkeypatch, gateway_on):
    """Купленный пакет оставляет след В ДЕНЬГАХ, а не только в правах.

    Это ровно тот случай, который прошёл живьём и не попал в учёт: пакет
    зачислен, `student_payment` пуст.
    """
    env = await _setup(db, "p615-land", price=550000)
    student_id = env["student_id"]
    txn = f"pkg-{uuid.uuid4().hex[:12]}"

    async def fake_fetch(payment_id: str) -> GatewayPayment:
        return _package_payment(payment_id=payment_id, student_id=student_id)

    monkeypatch.setattr(yookassa_service, "fetch_payment", fake_fetch)

    resp = await _webhook(client, txn)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "recorded"

    rows = await _payment_rows(db, student_id)
    assert len(rows) == 1, "деньги за пакет снова не попали в учёт"
    row = rows[0]
    assert row["purpose"] == "ai_package"
    assert row["amount_minor"] == PACKAGE_MINOR
    assert row["status"] == "confirmed", "деньги на счёте — подтверждать нечего"
    assert row["gateway_payment_id"] == txn
    # Месяца у покупки нет и быть не должно: она бессрочная и переносится.
    assert row["group_id"] is None and row["period"] is None

    granted = (
        await db.execute(
            text("SELECT granted FROM student_ai_grant WHERE gateway_payment_id = :t"),
            {"t": txn},
        )
    ).scalar_one()
    assert granted == PACKAGE_UNITS, "деньги учли, а пакет потеряли"


async def test_package_money_does_not_pay_for_the_month(db, client, monkeypatch, gateway_on):
    """Пакет за 500 ₽ не закрывает долг за обучение.

    Самая дорогая ошибка этой схемы: разовая покупка, попавшая в подсчёт месяца,
    молча уменьшает долг — человек считается заплатившим за то, за что не платил.
    """
    env = await _setup(db, "p615-debt", price=550000)
    student_id = env["student_id"]
    await _recalc(db, student_id=student_id)
    await _charge_id(db, student_id=student_id)

    before = await payment_service.list_student_charges(db, student_id=student_id)
    due_before = before[0]["due_minor"]
    assert due_before > 0, "проверять нечего: месяц и так без долга"

    async def fake_fetch(payment_id: str) -> GatewayPayment:
        return _package_payment(payment_id=payment_id, student_id=student_id)

    monkeypatch.setattr(yookassa_service, "fetch_payment", fake_fetch)
    assert (await _webhook(client, f"pkg-{uuid.uuid4().hex[:12]}")).status_code == 200

    after = await payment_service.list_student_charges(db, student_id=student_id)
    assert after[0]["due_minor"] == due_before, "покупка пакета погасила долг за месяц"
    assert after[0]["paid_minor"] == 0
    assert after[0]["payments"] == [], "покупка встала в историю платежей месяца"


async def test_repeat_delivery_does_not_double_the_money(db, client, monkeypatch, gateway_on):
    """Повтор доставки — штатный исход, а не вторые 500 ₽."""
    env = await _setup(db, "p615-idem", price=550000)
    student_id = env["student_id"]
    txn = f"pkg-{uuid.uuid4().hex[:12]}"

    async def fake_fetch(payment_id: str) -> GatewayPayment:
        return _package_payment(payment_id=payment_id, student_id=student_id)

    monkeypatch.setattr(yookassa_service, "fetch_payment", fake_fetch)

    first = await _webhook(client, txn)
    second = await _webhook(client, txn)
    assert first.json()["status"] == "recorded"
    assert second.json()["status"] == "already_recorded"

    rows = await _payment_rows(db, student_id)
    assert len(rows) == 1, "повторное уведомление завело вторые деньги"
    grants = (
        await db.execute(
            text("SELECT count(*) FROM student_ai_grant WHERE student_id = :s"),
            {"s": student_id},
        )
    ).scalar_one()
    assert grants == 1


async def test_missed_notification_is_closed_by_reconcile(db, client, monkeypatch, gateway_on):
    """Сверка дозакрывает покупку, о которой уведомление не дошло.

    До tsk-615 такой платёж возвращался в `without_charge` — сверка сама
    объявляла живые деньги необъяснимыми, и разница со шлюзом оставалась.
    """
    env = await _setup(db, "p615-recon", price=550000)
    student_id = env["student_id"]
    txn = f"pkg-{uuid.uuid4().hex[:12]}"

    async def fake_list(*, created_from: date, created_to: date):
        return [_package_payment(payment_id=txn, student_id=student_id)]

    monkeypatch.setattr(yookassa_service, "list_succeeded", fake_list)

    today = date.today().isoformat()
    resp = await client.post(
        f"/api/v1/marketer/payments/reconcile?date_from={today}&date_to={today}",
        headers=_auth(env["token"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["added"] == 1, body
    assert body["without_charge"] == [], "покупка снова объявлена платежом без начисления"

    rows = await _payment_rows(db, student_id)
    assert [r["purpose"] for r in rows] == ["ai_package"]
    granted = (
        await db.execute(
            text("SELECT granted FROM student_ai_grant WHERE gateway_payment_id = :t"),
            {"t": txn},
        )
    ).scalar_one()
    assert granted == PACKAGE_UNITS, "сверка учла деньги, но не выдала пакет"

    # Повторная сверка — обычное дело: маркетолог запускает её регулярно. Уже
    # закрытая покупка не должна объявляться расхождением, иначе список проблем
    # растёт с каждым прогоном и его перестают читать.
    again = await client.post(
        f"/api/v1/marketer/payments/reconcile?date_from={today}&date_to={today}",
        headers=_auth(env["token"]),
    )
    assert again.status_code == 200, again.text
    body2 = again.json()
    assert body2["added"] == 0
    assert body2["without_charge"] == [], "учтённая покупка помечена проблемной"
    assert body2["already_recorded"] == 1, body2
    assert len(await _payment_rows(db, student_id)) == 1


async def test_student_sees_the_purchase(db, client, monkeypatch, gateway_on):
    """Ученик видит, за что списаны деньги, — иначе покупка выглядит пропавшей."""
    env = await _setup(db, "p615-see", price=550000)
    student_id = env["student_id"]

    async def fake_fetch(payment_id: str) -> GatewayPayment:
        return _package_payment(payment_id=payment_id, student_id=student_id)

    monkeypatch.setattr(yookassa_service, "fetch_payment", fake_fetch)
    await _webhook(client, f"pkg-{uuid.uuid4().hex[:12]}")

    _, token = await _login_as(db, student_id)
    resp = await client.get("/api/v1/me/purchases", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["amount_minor"] == PACKAGE_MINOR
    assert body[0]["purpose"] == "ai_package"
    assert body[0]["purpose_name"] == "Пакет обращений к ИИ-наставнику"
    assert body[0]["status"] == "confirmed"


async def test_purchase_list_is_not_shared_between_students(db, client, monkeypatch, gateway_on):
    """Чужие покупки не видны: список денег строится по владельцу, а не по всем."""
    buyer = await _setup(db, "p615-own", price=550000)
    stranger = await _setup(db, "p615-alien", price=550000)

    async def fake_fetch(payment_id: str) -> GatewayPayment:
        return _package_payment(payment_id=payment_id, student_id=buyer["student_id"])

    monkeypatch.setattr(yookassa_service, "fetch_payment", fake_fetch)
    await _webhook(client, f"pkg-{uuid.uuid4().hex[:12]}")

    _, token = await _login_as(db, stranger["student_id"])
    resp = await client.get("/api/v1/me/purchases", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json() == []


async def test_marketer_sees_purchase_in_the_same_list(db, client, monkeypatch, gateway_on):
    """Разовая покупка идёт в общем списке платежей, а не в отдельной кассе.

    Соединения с начислением и тарифной группой должны быть левыми: внутреннее
    молча выбросило бы такую строку, и учёт остался бы ровно так же невидим.
    """
    env = await _setup(db, "p615-mkt", price=550000)
    student_id = env["student_id"]

    async def fake_fetch(payment_id: str) -> GatewayPayment:
        return _package_payment(payment_id=payment_id, student_id=student_id)

    monkeypatch.setattr(yookassa_service, "fetch_payment", fake_fetch)
    await _webhook(client, f"pkg-{uuid.uuid4().hex[:12]}")

    resp = await client.get(
        f"/api/v1/marketer/payments?student_id={student_id}", headers=_auth(env["token"])
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1, "покупка выпала из списка маркетолога"
    row = rows[0]
    assert row["purpose"] == "ai_package"
    assert row["period"] is None and row["group_id"] is None
    # Не ноль: ноль читался бы как «месяц оплачен полностью».
    assert row["charge_total_minor"] is None
    assert row["charge_due_minor"] is None

    only_monthly = await client.get(
        f"/api/v1/marketer/payments?student_id={student_id}&purpose=monthly",
        headers=_auth(env["token"]),
    )
    assert only_monthly.json() == [], "фильтр по назначению не работает"


async def test_purchase_falls_into_the_month_it_was_paid(db, client, monkeypatch, gateway_on):
    """Покупка попадает в сводку ТОГО месяца, когда за неё заплатили.

    Экран маркетолога всегда смотрит на конкретный месяц. Сравнение по `period`
    для разовой покупки не истинно никогда — без отдельного правила она выпадала
    бы из сводки, то есть осталась бы невидимой ровно так же, как до задачи.
    """
    env = await _setup(db, "p615-month", price=550000)
    student_id = env["student_id"]

    async def fake_fetch(payment_id: str) -> GatewayPayment:
        return _package_payment(payment_id=payment_id, student_id=student_id)

    monkeypatch.setattr(yookassa_service, "fetch_payment", fake_fetch)
    await _webhook(client, f"pkg-{uuid.uuid4().hex[:12]}")

    today = date.today()
    this_month = today.replace(day=1)
    other_month = (this_month - timedelta(days=1)).replace(day=1)

    in_month = await client.get(
        f"/api/v1/marketer/payments?student_id={student_id}&period={this_month}",
        headers=_auth(env["token"]),
    )
    assert [r["purpose"] for r in in_month.json()] == ["ai_package"], in_month.text

    # И не попадает в чужой месяц — иначе одна покупка считалась бы дважды.
    in_other = await client.get(
        f"/api/v1/marketer/payments?student_id={student_id}&period={other_month}",
        headers=_auth(env["token"]),
    )
    assert in_other.json() == []


async def test_export_covers_one_off_purchases(db, client, monkeypatch, gateway_on):
    """Выгрузка для сверки включает покупку — иначе шлюз всегда богаче системы."""
    env = await _setup(db, "p615-export", price=550000)
    student_id = env["student_id"]

    async def fake_fetch(payment_id: str) -> GatewayPayment:
        return _package_payment(payment_id=payment_id, student_id=student_id)

    monkeypatch.setattr(yookassa_service, "fetch_payment", fake_fetch)
    await _webhook(client, f"pkg-{uuid.uuid4().hex[:12]}")

    today = date.today()
    rows = await payment_service.export_confirmed(db, date_from=today, date_to=today)
    mine = [r for r in rows if r["id"] in {p["id"] for p in await _ids(db, student_id)}]
    assert mine, "покупка не попала в выгрузку за день платежа"
    assert mine[0]["purpose"] == "ai_package"
    assert mine[0]["period"] is None
    assert mine[0]["group_name"] is None


async def _ids(db, student_id: int) -> list[dict]:
    rows = (
        await db.execute(
            text("SELECT id FROM student_payment WHERE student_id = :s"),
            {"s": student_id},
        )
    ).all()
    return [{"id": int(r.id)} for r in rows]


async def test_schema_keeps_purpose_and_month_in_sync(db):
    """Схема не даёт перепутать назначение с месяцем ни в одну сторону.

    Это замена утраченному `NOT NULL`: месячный платёж без месяца выпал бы из
    подсчёта оплаченности, а разовый С месяцем — наоборот, погасил бы чужой долг.
    """
    student_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO users (full_name, email, is_active) "
                    "VALUES ('tsk615 схема', :e, true) RETURNING id"
                ),
                {"e": f"tsk615-{uuid.uuid4().hex[:12]}@example.test"},
            )
        ).scalar_one()
    )
    await db.commit()

    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO student_payment (student_id, amount_minor, method, "
                    "                             purpose, status) "
                    "VALUES (:s, 100, 'manual', 'monthly', 'pending')"
                ),
                {"s": student_id},
            )

    # Разовая покупка с месяцем: группа пуста, поэтому внешний ключ такую строку
    # не проверяет вовсе (`MATCH SIMPLE`) — отбить её обязан именно CHECK.
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO student_payment (student_id, period, amount_minor, "
                    "                             method, purpose, status) "
                    "VALUES (:s, DATE '2026-08-01', 100, 'manual', 'ai_package', 'pending')"
                ),
                {"s": student_id},
            )


async def test_unknown_purpose_is_rejected_by_the_database(db):
    """Описка в назначении не создаёт третий класс денег, невидимый нигде."""
    student_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO users (full_name, email, is_active) "
                    "VALUES ('tsk615 описка', :e, true) RETURNING id"
                ),
                {"e": f"tsk615-typo-{uuid.uuid4().hex[:12]}@example.test"},
            )
        ).scalar_one()
    )
    await db.commit()

    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO student_payment (student_id, amount_minor, method, "
                    "                             purpose, status) "
                    "VALUES (:s, 100, 'manual', 'ai_pakage', 'pending')"
                ),
                {"s": student_id},
            )


async def test_grant_without_payment_is_still_repaired(db):
    """Пакет уже выдан, а денег нет — сверка обязана дописать именно деньги.

    Так выглядят покупки, случившиеся до этой задачи: грант в базе есть,
    платежа нет. Повторный проход не должен ни выдать второй пакет, ни
    промолчать про деньги.
    """
    student_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO users (full_name, email, is_active) "
                    "VALUES ('tsk615 задним числом', :e, true) RETURNING id"
                ),
                {"e": f"tsk615-back-{uuid.uuid4().hex[:12]}@example.test"},
            )
        ).scalar_one()
    )
    txn = f"pkg-old-{uuid.uuid4().hex[:12]}"
    await subscription_service.grant_ai_package(
        db, student_id, units=PACKAGE_UNITS, gateway_payment_id=txn, note="оплачено картой"
    )
    await db.commit()

    granted, recorded = await subscription_service.record_ai_package_purchase(
        db, student_id, units=PACKAGE_UNITS, gateway_payment_id=txn,
        amount_minor=PACKAGE_MINOR,
    )
    assert granted is False, "выдан второй пакет за те же деньги"
    assert recorded is True, "деньги остались неучтёнными"

    rows = await _payment_rows(db, student_id)
    assert [r["purpose"] for r in rows] == ["ai_package"]
    assert rows[0]["amount_minor"] == PACKAGE_MINOR
