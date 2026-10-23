"""tsk-1254 — разовая покупка курса не ломает помесячные деньги.

Главные риски задачи — в соседних деньгах, а не в самой покупке:

* разовый тариф не должен попасть в помесячный подбор: до tsk-1254 расчёт поле
  `period` не читал, и тариф «курс целиком за 50 000» начислялся бы каждый месяц;
* платёж за курс не должен гасить месячный долг по другой группе;
* купленная группа выходит из помесячного счёта, а месяц перехода считается
  долей (настройка `course_purchase_prorate_first_month`);
* продажа закрыта рубильником, повтор уведомления шлюза не заводит вторые деньги,
  истёкший срок закрывает сдачу только при включённом ACCESS_EXPIRY_ENABLED.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.core import settings_store
from app.services import charge_service, course_purchase_service, payment_service, yookassa_service
from app.services.yookassa_service import GatewayPayment
from app.utils.exceptions import DomainError
from tests.test_tsk505_marketer_pricing import _enroll, _new_course, _new_group, _price_course
from tests.test_tsk511_charges_breaks import MONDAYS, NEXT_PERIOD, PERIOD, _setup

pytestmark = pytest.mark.asyncio

MONTH_MINOR = 550000
COURSE_MINOR = 4_500_000


@pytest.fixture
def sales_on(monkeypatch):
    monkeypatch.setenv("COURSE_PURCHASE_ENABLED", "true")
    return True


async def _add_once_tariff(db, group_id: int, *, price: int = COURSE_MINOR, months: int = 12) -> int:
    tariff_id = (
        await db.execute(
            text(
                "INSERT INTO pricing_tariff (group_id, name, price_minor, period, access_months, "
                " installments_allowed, sort_order) "
                "VALUES (:g, 'Курс целиком', :p, 'once', :m, true, 99) RETURNING id"
            ),
            {"g": group_id, "p": price, "m": months},
        )
    ).scalar_one()
    await db.commit()
    return int(tariff_id)


async def _charges(db, student_id: int, period: date = PERIOD) -> dict[int, int]:
    """Итоги месяца по группам (ручная сумма главнее расчётной)."""
    rows = (
        await db.execute(
            text(
                "SELECT group_id, COALESCE(manual_minor, calculated_minor) AS total "
                "  FROM student_monthly_charge WHERE student_id = :s AND period = :p"
            ),
            {"s": student_id, "p": period},
        )
    ).all()
    return {int(r.group_id): int(r.total) for r in rows}


async def _buy(db, env: dict, tariff_id: int, *, paid_on: date) -> int:
    purchase = await course_purchase_service.create_pending(
        db, student_id=env["student_id"], course_id=env["course_id"], tariff_id=tariff_id
    )
    await db.commit()
    assert await course_purchase_service.mark_paid(
        db, purchase_id=purchase["id"], amount_minor=COURSE_MINOR, paid_on=paid_on
    )
    return purchase["id"]


async def test_monthly_charge_ignores_once_tariff(db):
    """Месячный расчёт не видит разовые тарифы.

    Группа с двумя вариантами (месяц и «курс целиком») считает месяц по
    месячному. Группа, где есть ТОЛЬКО разовый тариф, помесячно не начисляется
    вовсе — а не начисляет 45 000 каждый месяц.
    """
    env = await _setup(db, "p1254-mix", price=MONTH_MINOR)
    await _add_once_tariff(db, env["group_id"])
    await charge_service.recalculate_for_student(db, student_id=env["student_id"], period=PERIOD)
    assert await _charges(db, env["student_id"]) == {env["group_id"]: MONTH_MINOR}

    only_once = await _setup(db, "p1254-once", price=MONTH_MINOR)
    await db.execute(
        text("UPDATE pricing_tariff SET is_active = false WHERE group_id = :g AND period = 'month'"),
        {"g": only_once["group_id"]},
    )
    await db.commit()
    await _add_once_tariff(db, only_once["group_id"])
    await charge_service.recalculate_for_student(
        db, student_id=only_once["student_id"], period=PERIOD
    )
    assert await _charges(db, only_once["student_id"]) == {}, (
        "разовый тариф начислился помесячно"
    )


async def test_course_payment_does_not_settle_monthly_debt(db, sales_on):
    """Платёж за курс не гасит месячный долг по другой группе.

    Ученик платит помесячно за группу A и покупает целиком курс группы B.
    Долг по A остаётся ровно прежним, а платёж за курс лежит без группы и месяца.
    """
    env = await _setup(db, "p1254-debt", price=MONTH_MINOR)
    await charge_service.recalculate_for_student(db, student_id=env["student_id"], period=PERIOD)
    before = await payment_service.list_student_charges(db, student_id=env["student_id"])
    due_before = {c["group_id"]: c["due_minor"] for c in before}
    assert due_before[env["group_id"]] == MONTH_MINOR

    group_b = await _new_group(db, "p1254-b", [("Месяц", 300000, None, None)])
    course_b = await _new_course(db, "p1254-b")
    await _price_course(db, course_id=course_b, group_id=group_b)
    await _enroll(db, student_id=env["student_id"], course_id=course_b)
    tariff = await _add_once_tariff(db, group_b)
    await _buy(db, {**env, "course_id": course_b}, tariff, paid_on=PERIOD)

    after = await payment_service.list_student_charges(db, student_id=env["student_id"])
    due_after = {c["group_id"]: c["due_minor"] for c in after}
    assert due_after[env["group_id"]] == MONTH_MINOR, "платёж за курс погасил чужой месячный долг"
    assert group_b not in due_after or due_after[group_b] == 0

    pay = (
        await db.execute(
            text(
                "SELECT purpose, group_id, period, amount_minor FROM student_payment "
                " WHERE student_id = :s AND purpose = 'course'"
            ),
            {"s": env["student_id"]},
        )
    ).one()
    assert (pay.group_id, pay.period, pay.amount_minor) == (None, None, COURSE_MINOR)


async def test_purchase_removes_group_from_monthly_billing(db, sales_on):
    """Купил с первого числа — месяц покупки и следующие помесячно не начисляются."""
    env = await _setup(db, "p1254-full", price=MONTH_MINOR)
    await charge_service.recalculate_for_student(db, student_id=env["student_id"], period=PERIOD)
    assert await _charges(db, env["student_id"]) == {env["group_id"]: MONTH_MINOR}
    tariff = await _add_once_tariff(db, env["group_id"])

    await _buy(db, env, tariff, paid_on=PERIOD)
    await charge_service.recalculate_for_student(db, student_id=env["student_id"], period=PERIOD)
    assert await _charges(db, env["student_id"]) == {}, "открытый месяц покупки не снят"
    await charge_service.recalculate_month(db, period=NEXT_PERIOD)
    assert await _charges(db, env["student_id"], NEXT_PERIOD) == {}

    until = (
        await db.execute(
            text("SELECT access_until FROM user_courses WHERE user_id = :s AND course_id = :c"),
            {"s": env["student_id"], "c": env["course_id"]},
        )
    ).scalar_one()
    assert until == course_purchase_service.access_until_for(PERIOD, 12)


async def test_midmonth_purchase_bills_share_of_month(db, sales_on, monkeypatch):
    """Перевод посреди месяца: доля месяца до покупки (по умолчанию) или ничего."""
    env = await _setup(db, "p1254-half", price=MONTH_MINOR)
    tariff = await _add_once_tariff(db, env["group_id"])
    await charge_service.recalculate_for_student(db, student_id=env["student_id"], period=PERIOD)
    # Покупка в день третьего понедельника: оплачиваются два занятия из четырёх.
    await _buy(db, env, tariff, paid_on=MONDAYS[2])
    assert await _charges(db, env["student_id"]) == {env["group_id"]: MONTH_MINOR // 2}

    monkeypatch.setitem(settings_store._cabinet, "course_purchase_prorate_first_month", False)
    await charge_service.recalculate_for_student(db, student_id=env["student_id"], period=PERIOD)
    assert await _charges(db, env["student_id"]) == {}


async def test_sales_switch_off_by_default(db, monkeypatch):
    """Без COURSE_PURCHASE_ENABLED покупку не завести."""
    monkeypatch.delenv("COURSE_PURCHASE_ENABLED", raising=False)
    env = await _setup(db, "p1254-off", price=MONTH_MINOR)
    tariff = await _add_once_tariff(db, env["group_id"])
    with pytest.raises(DomainError) as exc:
        await course_purchase_service.create_pending(
            db, student_id=env["student_id"], course_id=env["course_id"], tariff_id=tariff
        )
    assert exc.value.status_code == 403


async def test_wrong_amount_or_monthly_tariff_rejected(db, sales_on):
    """Месячный тариф целиком не продаётся; сумма не по цене не открывает доступ."""
    env = await _setup(db, "p1254-wrong", price=MONTH_MINOR)
    monthly = (
        await db.execute(
            text("SELECT id FROM pricing_tariff WHERE group_id = :g"), {"g": env["group_id"]}
        )
    ).scalar_one()
    with pytest.raises(DomainError):
        await course_purchase_service.create_pending(
            db, student_id=env["student_id"], course_id=env["course_id"], tariff_id=monthly
        )
    tariff = await _add_once_tariff(db, env["group_id"])
    purchase = await course_purchase_service.create_pending(
        db, student_id=env["student_id"], course_id=env["course_id"], tariff_id=tariff
    )
    await db.commit()
    with pytest.raises(DomainError):
        await course_purchase_service.mark_paid(
            db, purchase_id=purchase["id"], amount_minor=COURSE_MINOR - 1, paid_on=PERIOD
        )
    status_ = (
        await db.execute(
            text("SELECT status FROM course_purchase WHERE id = :id"), {"id": purchase["id"]}
        )
    ).scalar_one()
    assert status_ == "pending"


async def test_gateway_webhook_is_idempotent(db, client, sales_on, monkeypatch):
    """Повтор уведомления шлюза не заводит вторые деньги."""
    monkeypatch.setattr(yookassa_service.settings, "yookassa_shop_id", "1426025")
    monkeypatch.setattr(yookassa_service.settings, "yookassa_secret_key", "test_secret")
    monkeypatch.setattr(yookassa_service.settings, "yookassa_allow_live", False)
    env = await _setup(db, "p1254-hook", price=MONTH_MINOR)
    tariff = await _add_once_tariff(db, env["group_id"])
    purchase = await course_purchase_service.create_pending(
        db, student_id=env["student_id"], course_id=env["course_id"], tariff_id=tariff
    )
    await db.commit()
    txn = f"course-{uuid.uuid4().hex[:12]}"

    async def fake_fetch(payment_id: str) -> GatewayPayment:
        return GatewayPayment(
            id=payment_id, status="succeeded", amount_minor=COURSE_MINOR, paid=True,
            confirmation_url=None, test=True,
            metadata={
                "purpose": "course",
                "student_id": str(env["student_id"]),
                "course_purchase_id": str(purchase["id"]),
            },
        )

    monkeypatch.setattr(yookassa_service, "fetch_payment", fake_fetch)
    body = {"type": "notification", "event": "payment.succeeded", "object": {"id": txn}}
    first = await client.post("/api/v1/payments/yookassa/webhook", json=body)
    second = await client.post("/api/v1/payments/yookassa/webhook", json=body)
    assert first.json()["status"] == "recorded", first.text
    assert second.json()["status"] == "already_recorded", second.text
    count = (
        await db.execute(
            text("SELECT count(*) FROM student_payment WHERE course_purchase_id = :p"),
            {"p": purchase["id"]},
        )
    ).scalar_one()
    assert count == 1


async def test_expired_access_closes_answers_only_when_switch_on(db, monkeypatch):
    """Истёкший срок закрывает сдачу только при ACCESS_EXPIRY_ENABLED."""
    env = await _setup(db, "p1254-exp", price=MONTH_MINOR)
    await db.execute(
        text("UPDATE user_courses SET access_until = :d WHERE user_id = :s AND course_id = :c"),
        {"d": date.today() - timedelta(days=1), "s": env["student_id"], "c": env["course_id"]},
    )
    await db.commit()

    monkeypatch.delenv("ACCESS_EXPIRY_ENABLED", raising=False)
    await course_purchase_service.assert_access_not_expired(
        db, student_id=env["student_id"], course_id=env["course_id"]
    )

    monkeypatch.setenv("ACCESS_EXPIRY_ENABLED", "true")
    with pytest.raises(DomainError) as exc:
        await course_purchase_service.assert_access_not_expired(
            db, student_id=env["student_id"], course_id=env["course_id"]
        )
    assert exc.value.payload["code"] == course_purchase_service.ACCESS_EXPIRED_CODE

    # Продление от сегодня открывает сдачу снова.
    until = await course_purchase_service.extend_access(
        db, student_id=env["student_id"], course_id=env["course_id"], months=1
    )
    assert until is not None and until >= date.today()
    await course_purchase_service.assert_access_not_expired(
        db, student_id=env["student_id"], course_id=env["course_id"]
    )


async def test_expiry_notice_once_per_threshold(db, monkeypatch):
    """Напоминание о конце доступа — один раз на рубеж 30 и 7 дней."""
    monkeypatch.setenv("ACCESS_EXPIRY_ENABLED", "true")
    env = await _setup(db, "p1254-note", price=MONTH_MINOR)
    today = date.today()
    await db.execute(
        text("UPDATE user_courses SET access_until = :d WHERE user_id = :s AND course_id = :c"),
        {"d": today + timedelta(days=20), "s": env["student_id"], "c": env["course_id"]},
    )
    await db.commit()

    async def _count() -> int:
        return (
            await db.execute(
                text("SELECT count(*) FROM notifications WHERE user_id = :u AND kind = :k"),
                {"u": env["student_id"], "k": course_purchase_service.EXPIRY_NOTICE_KIND},
            )
        ).scalar_one()

    await course_purchase_service.send_expiry_notices(db, today=today)
    await course_purchase_service.send_expiry_notices(db, today=today)
    assert await _count() == 1, "напоминание на рубеже 30 дней ушло дважды"
    await course_purchase_service.send_expiry_notices(db, today=today + timedelta(days=14))
    assert await _count() == 2, "рубеж 7 дней не напомнил"


async def test_purchase_refused_when_group_has_sibling_course(db, sales_on):
    """Второй курс той же группы не должен стать бесплатным вместе с купленным."""
    env = await _setup(db, "p1254-sib", price=MONTH_MINOR)
    sibling = await _new_course(db, "p1254-sib2")
    await _price_course(db, course_id=sibling, group_id=env["group_id"])
    await _enroll(db, student_id=env["student_id"], course_id=sibling)
    tariff = await _add_once_tariff(db, env["group_id"])
    with pytest.raises(DomainError) as exc:
        await course_purchase_service.create_pending(
            db, student_id=env["student_id"], course_id=env["course_id"], tariff_id=tariff
        )
    assert exc.value.status_code == 409


async def test_second_click_reuses_pending_and_paid_is_refused(db, sales_on):
    """Два клика — одна покупка; после оплаты новую завести нельзя."""
    env = await _setup(db, "p1254-dbl", price=MONTH_MINOR)
    tariff = await _add_once_tariff(db, env["group_id"])
    args = dict(student_id=env["student_id"], course_id=env["course_id"], tariff_id=tariff)
    first = await course_purchase_service.create_pending(db, **args)
    await db.commit()
    second = await course_purchase_service.create_pending(db, **args)
    assert first["id"] == second["id"]
    await course_purchase_service.mark_paid(
        db, purchase_id=first["id"], amount_minor=COURSE_MINOR, paid_on=date.today()
    )
    with pytest.raises(DomainError) as exc:
        await course_purchase_service.create_pending(db, **args)
    assert exc.value.status_code == 409


async def test_refund_returns_group_to_monthly_billing(db, sales_on):
    """Возврат: покупка перестаёт снимать месяц, платёж не считается выручкой."""
    env = await _setup(db, "p1254-ref", price=MONTH_MINOR)
    tariff = await _add_once_tariff(db, env["group_id"])
    purchase_id = await _buy(db, env, tariff, paid_on=PERIOD)
    await charge_service.recalculate_for_student(db, student_id=env["student_id"], period=PERIOD)
    assert await _charges(db, env["student_id"]) == {}

    assert await course_purchase_service.refund(db, purchase_id=purchase_id, staff_id=None, note="тест")
    await charge_service.recalculate_for_student(db, student_id=env["student_id"], period=PERIOD)
    assert await _charges(db, env["student_id"]) == {env["group_id"]: MONTH_MINOR}
    status_ = (
        await db.execute(
            text("SELECT status FROM student_payment WHERE course_purchase_id = :p"),
            {"p": purchase_id},
        )
    ).scalar_one()
    assert status_ == "reversed"


def test_access_until_and_extension_keep_the_same_boundary():
    """Год с 23.10 — по 22.10; продление на год — по 22.10 следующего года."""
    assert course_purchase_service.access_until_for(date(2026, 10, 23), 12) == date(2027, 10, 22)
    assert course_purchase_service.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)


async def test_offers_listed_for_student_only_when_sales_on(db, client, monkeypatch):
    """Кабинету нужен tariff_id разового тарифа — он приходит в offers."""
    from tests.test_tsk010_payments import _login_as

    env = await _setup(db, "p1254-offer", price=MONTH_MINOR)
    tariff = await _add_once_tariff(db, env["group_id"])
    _, token = await _login_as(db, env["student_id"])
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.delenv("COURSE_PURCHASE_ENABLED", raising=False)
    off = (await client.get("/api/v1/me/course-purchases", headers=headers)).json()
    assert off["sales_enabled"] is False and off["offers"] == []
    monkeypatch.setenv("COURSE_PURCHASE_ENABLED", "true")
    on = (await client.get("/api/v1/me/course-purchases", headers=headers)).json()
    assert [o["tariff_id"] for o in on["offers"]] == [tariff]
    assert on["offers"][0]["access_months"] == 12
