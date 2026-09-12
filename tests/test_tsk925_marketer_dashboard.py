"""tsk-925 — дашборд маркетолога: агрегаты шести KPI-плиток.

Не проверяем «экран открывается» саму по себе — деньги и переходы обязаны
совпадать с уже проверенными формулами денежного контура (`charge_service`,
tsk-010) и с экраном долгов ушедших (`payment_reminder_service`, tsk-805), а
предикат «клиент» — с тем, что подтвердил `/db-check` на боевой базе перед
реализацией: `base_legacy` — код технической пакетной миграции (tsk-301), не
сигнал воронки продаж, и не должен считаться новым клиентом.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services import charge_service, marketer_dashboard_service, payment_reminder_service
from tests.test_tsk010_payments import _recalc
from tests.test_tsk505_marketer_pricing import _auth, _new_group, _new_user
from tests.test_tsk511_charges_breaks import PERIOD as CHARGE_PERIOD
from tests.test_tsk511_charges_breaks import _setup as _charge_setup
from tests.test_tsk805_alumni_debts import _close_month, _make_alumni

pytestmark = pytest.mark.asyncio

_TAG = "tsk925"

#: Фиксированный тестовый месяц вдали от боевых данных — снижает риск
#: коллизии с посевными строками dev-БД. Проверки всё равно идут «до/после»,
#: а не на голом равенстве нулю — на случай, если коллизия всё же есть.
_TEST_MONTH = date(2019, 6, 1)


async def _plan_id(db, code: str) -> int:
    return (
        await db.execute(text("SELECT id FROM subscription_plan WHERE code = :c"), {"c": code})
    ).scalar_one()


async def _insert_lead(
    db,
    *,
    created_at: datetime,
    note: str | None = None,
    linked_student_id: int | None = None,
) -> int:
    source_id = (await db.execute(text("SELECT id FROM lead_source ORDER BY id LIMIT 1"))).scalar_one()
    lead_id = (
        await db.execute(
            text(
                "INSERT INTO leads (source_id, contact, note, linked_student_id, created_at) "
                "VALUES (:src, :contact, :note, :linked, :created_at) RETURNING id"
            ),
            {
                "src": source_id,
                "contact": f"{_TAG}-{random.randint(10**6, 10**7)}",
                "note": note,
                "linked": linked_student_id,
                "created_at": created_at,
            },
        )
    ).scalar_one()
    await db.commit()
    return int(lead_id)


async def _insert_subscription(
    db, *, student_id: int, plan_code: str, starts_on: date, pricing_group_id: int | None
) -> None:
    plan_id = await _plan_id(db, plan_code)
    await db.execute(
        text(
            "INSERT INTO student_subscription (student_id, plan_id, pricing_group_id, starts_on) "
            "VALUES (:s, :p, :g, :d)"
        ),
        {"s": student_id, "p": plan_id, "g": pricing_group_id, "d": starts_on},
    )
    await db.commit()


@pytest.mark.parametrize("role", ["teacher", "student", "methodist", None])
async def test_dashboard_gate_rejects_other_roles(db, client, role):
    _, token = await _new_user(db, role=role, name=f"gate-{role}")
    resp = await client.get("/api/v1/marketer/dashboard", headers=_auth(token))
    assert resp.status_code == 403, resp.text


async def test_dashboard_endpoint_smoke(db, client):
    _, token = await _new_user(db, role="marketer", name=f"{_TAG}-smoke")
    resp = await client.get(
        f"/api/v1/marketer/dashboard?period={_TEST_MONTH.isoformat()}&months=3",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["period"] == _TEST_MONTH.isoformat()
    assert len(body["leads"]["history"]) == 3
    assert "conversion" in body and "current_period_is_partial" in body["conversion"]


async def test_leads_counted_by_month_of_creation(db):
    before = await marketer_dashboard_service.get_dashboard(db, period=_TEST_MONTH, months=1)

    await _insert_lead(db, created_at=datetime(2019, 6, 15, tzinfo=timezone.utc))
    await _insert_lead(db, created_at=datetime(2019, 6, 20, tzinfo=timezone.utc))
    # За пределами месяца — не должен попасть в счётчик июня.
    await _insert_lead(db, created_at=datetime(2019, 5, 20, tzinfo=timezone.utc))

    after = await marketer_dashboard_service.get_dashboard(db, period=_TEST_MONTH, months=1)
    assert after.leads.current - before.leads.current == 2


async def test_base_legacy_is_not_counted_as_new_client(db):
    """Ровно то, что подтвердил /db-check на боевой базе (2026-09-12, tsk-925):

    08.08.2026 в `base_legacy` разом легло 37 строк с причиной «tsk-301 этап 5:
    пакетная миграция» — код тарифа сам по себе не отличает миграцию от
    органической продажи, поэтому предикат «клиент» его явно исключает.
    """
    tag = f"{_TAG}-legacy"
    group_id = await _new_group(db, tag, [])
    s_base, _ = await _new_user(db, role="student", name=f"{tag}-base")
    s_legacy, _ = await _new_user(db, role="student", name=f"{tag}-legacy")
    s_alumni, _ = await _new_user(db, role="student", name=f"{tag}-alumni")

    before = await marketer_dashboard_service.get_dashboard(db, period=_TEST_MONTH, months=1)

    await _insert_subscription(
        db, student_id=s_base, plan_code="base", starts_on=_TEST_MONTH, pricing_group_id=group_id
    )
    await _insert_subscription(
        db, student_id=s_legacy, plan_code="base_legacy", starts_on=_TEST_MONTH, pricing_group_id=group_id
    )
    await _insert_subscription(
        db, student_id=s_alumni, plan_code="alumni", starts_on=_TEST_MONTH, pricing_group_id=None
    )

    after = await marketer_dashboard_service.get_dashboard(db, period=_TEST_MONTH, months=1)
    assert after.clients_alumni.clients.current - before.clients_alumni.clients.current == 1, (
        "base_legacy — техническая миграция (tsk-301), не должна считаться новым клиентом"
    )
    assert after.clients_alumni.alumni.current - before.clients_alumni.alumni.current == 1


async def test_conversion_counts_lead_that_became_client_in_a_later_month(db):
    """Конверсия — по когорте месяца появления лида, даже если переход случился позже."""
    tag = f"{_TAG}-conv"
    group_id = await _new_group(db, tag, [])
    student_id, _ = await _new_user(db, role="student", name=tag)

    before = await marketer_dashboard_service.get_dashboard(db, period=_TEST_MONTH, months=1)

    await _insert_lead(
        db, created_at=datetime(2019, 6, 10, tzinfo=timezone.utc), linked_student_id=student_id
    )
    # Стал клиентом только в СЛЕДУЮЩЕМ месяце — когорта июня обязана его увидеть.
    await _insert_subscription(
        db, student_id=student_id, plan_code="base", starts_on=date(2019, 7, 5), pricing_group_id=group_id
    )

    after = await marketer_dashboard_service.get_dashboard(db, period=_TEST_MONTH, months=1)
    assert after.conversion.current_leads_count - before.conversion.current_leads_count == 1
    assert after.conversion.current_converted_count - before.conversion.current_converted_count == 1


async def test_unprocessed_leads_respects_threshold_and_exclusions(db):
    now = datetime.now(timezone.utc)
    before = await marketer_dashboard_service.get_dashboard(
        db, period=_TEST_MONTH, months=1, unprocessed_threshold_days=3
    )

    await _insert_lead(db, created_at=now - timedelta(days=10))  # старый, без note/линка — считается
    await _insert_lead(db, created_at=now - timedelta(days=1))  # свежий — рано
    await _insert_lead(db, created_at=now - timedelta(days=10), note="звонили")  # уже обработан
    someone, _ = await _new_user(db, role="student", name=f"{_TAG}-unproc")
    await _insert_lead(db, created_at=now - timedelta(days=10), linked_student_id=someone)  # уже привязан

    after = await marketer_dashboard_service.get_dashboard(
        db, period=_TEST_MONTH, months=1, unprocessed_threshold_days=3
    )
    assert after.unprocessed_leads_count - before.unprocessed_leads_count == 1


async def test_charges_kpi_matches_charge_service_total(db):
    """Сумма месяца — через готовую формулу, не пересчитана параллельно."""
    env = await _charge_setup(db, f"{_TAG}-charges", price=550000)
    await _recalc(db, student_id=env["student_id"], period=CHARGE_PERIOD)

    expected_rows = await charge_service.list_charges(db, period=CHARGE_PERIOD)
    expected_total = sum(r["total_minor"] for r in expected_rows)

    result = await marketer_dashboard_service.get_dashboard(db, period=CHARGE_PERIOD, months=1)
    assert result.charges.current == expected_total
    assert expected_total > 0, "иначе проверка ничего не проверяет"


async def test_alumni_debt_tile_matches_alumni_debts_service(db):
    """Плитка долга ушедших — то же число, что уже проверенный экран tsk-805."""
    env = await _charge_setup(db, f"{_TAG}-alumni-debt", price=550000)
    await _recalc(db, student_id=env["student_id"], period=CHARGE_PERIOD)
    await _close_month(db, env["student_id"])
    await _make_alumni(db, env["student_id"])

    expected = await payment_reminder_service.list_alumni_debts(db)
    expected_total = sum(d.due_minor for d in expected)

    result = await marketer_dashboard_service.get_dashboard(db, period=CHARGE_PERIOD, months=1)
    assert result.alumni_debt_total_minor == expected_total
    assert result.alumni_debt_count == len(expected)
    assert any(d.student_id == env["student_id"] for d in expected), "иначе проверка ничего не проверяет"
