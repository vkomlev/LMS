"""tsk-805 — долг ушедшего ученика: отдельный список и адресное письмо.

Выпуск замораживает месяцы ученика (tsk-673), а рассылка напоминаний отбирала
должников по `status = 'open'` — ушедший с настоящим долгом выпадал из неё
навсегда. Здесь проверяем обе половины решения: закрытый месяц долг не прощает,
а письмо ушедшему уходит по кнопке, а не веером.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from app.services import notification_email_service, payment_reminder_service, payment_service
from tests.test_tsk010_reminders import _set_email, mail_ok  # noqa: F401 — фикстура
from tests.test_tsk010_payments import _recalc
from tests.test_tsk511_charges_breaks import _setup, PERIOD

pytestmark = pytest.mark.asyncio

OVERDUE_DAY = payment_service.due_date_for(PERIOD) + timedelta(days=1)


async def _close_month(db, student_id: int) -> None:
    """Заморозить месяцы ученика ровно так, как это делает выпуск."""
    await db.execute(
        text(
            "UPDATE student_monthly_charge SET status = 'closed', closed_at = now() "
            " WHERE student_id = :s AND status = 'open'"
        ),
        {"s": student_id},
    )
    await db.commit()


async def _make_alumni(db, student_id: int) -> None:
    """Перевести ученика на тариф «Выпускник» (`course_work = false`)."""
    plan_id = (
        await db.execute(
            text("SELECT id FROM subscription_plan WHERE code = 'alumni'")
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO student_subscription (student_id, plan_id, starts_on) "
            "VALUES (:s, :p, CURRENT_DATE)"
        ),
        {"s": student_id, "p": plan_id},
    )
    await db.commit()


async def test_closed_month_keeps_the_learner_in_the_mailing(db):
    """Закрытый месяц обычного ученика долг не прощает.

    Так стреляла бы кнопка «Закрыть месяц»: до tsk-805 первое её нажатие убрало
    бы из рассылки всех должников месяца разом.
    """
    env = await _setup(db, "tsk805-open", price=550000)
    await _recalc(db, student_id=env["student_id"])
    await _set_email(db, env["student_id"], "closed-month@example.com")

    before = await payment_reminder_service.list_overdue(db, today=OVERDUE_DAY)
    assert any(d.student_id == env["student_id"] for d in before)

    await _close_month(db, env["student_id"])

    after = await payment_reminder_service.list_overdue(db, today=OVERDUE_DAY)
    assert any(
        d.student_id == env["student_id"] for d in after
    ), "закрытие месяца замораживает сумму, а не прощает долг"


async def test_alumni_leaves_the_common_mailing_for_his_own_list(db):
    """Ушедший уходит из общей рассылки в отдельный список — и не пропадает."""
    env = await _setup(db, "tsk805-alum", price=550000)
    await _recalc(db, student_id=env["student_id"])
    await _set_email(db, env["student_id"], "alumni-debt@example.com")
    await _close_month(db, env["student_id"])
    await _make_alumni(db, env["student_id"])

    common = await payment_reminder_service.list_overdue(db, today=OVERDUE_DAY)
    assert not any(
        d.student_id == env["student_id"] for d in common
    ), "письмо ушедшему не должно уходить веером вместе со всеми"

    own = await payment_reminder_service.list_alumni_debts(db, today=OVERDUE_DAY)
    mine = [d for d in own if d.student_id == env["student_id"]]
    assert len(mine) == 1, "долг ушедшего обязан остаться на виду"
    assert mine[0].due_minor == 550000
    assert mine[0].is_overdue is True


async def test_alumni_debt_shows_before_the_due_date(db):
    """Ушедшему в середине месяца платить ещё только предстоит — долг уже виден.

    Просрочка здесь не условие попадания в список: иначе строка всплыла бы через
    месяц на экране, куда никто не листает.
    """
    env = await _setup(db, "tsk805-early", price=550000)
    await _recalc(db, student_id=env["student_id"])
    await _close_month(db, env["student_id"])
    await _make_alumni(db, env["student_id"])

    early = PERIOD + timedelta(days=5)
    debts = await payment_reminder_service.list_alumni_debts(db, today=early)
    mine = [d for d in debts if d.student_id == env["student_id"]]
    assert len(mine) == 1
    assert mine[0].is_overdue is False, "срок оплаты ещё не прошёл"


async def test_paid_alumni_is_out_of_the_list(db):
    """Заплатил — из списка пропал. Список живёт долгом, а не тарифом."""
    env = await _setup(db, "tsk805-paid", price=550000)
    await _recalc(db, student_id=env["student_id"])
    await _close_month(db, env["student_id"])
    await _make_alumni(db, env["student_id"])
    await db.execute(
        text(
            "INSERT INTO student_payment "
            "(student_id, group_id, period, amount_minor, method, status, "
            " reviewed_at, purpose) "
            "VALUES (:s, :g, :p, 550000, 'manual', 'confirmed', now(), 'monthly')"
        ),
        {"s": env["student_id"], "g": env["group_id"], "p": PERIOD},
    )
    await db.commit()

    debts = await payment_reminder_service.list_alumni_debts(db, today=OVERDUE_DAY)
    assert not any(d.student_id == env["student_id"] for d in debts)


async def test_pointed_reminder_goes_once_a_week(db, mail_ok):  # noqa: F811
    """Кнопка «напомнить» шлёт письмо, но второе нажатие подряд — уже нет."""
    env = await _setup(db, "tsk805-send", price=550000)
    await _recalc(db, student_id=env["student_id"])
    await _set_email(db, env["student_id"], "alumni-send@example.com")
    await _close_month(db, env["student_id"])
    await _make_alumni(db, env["student_id"])

    first = await payment_reminder_service.send_alumni_reminder(
        db, student_id=env["student_id"], sent_by=env["student_id"], today=OVERDUE_DAY
    )
    assert len(first.sent) == 1, first
    assert mail_ok == ["alumni-send@example.com"]

    second = await payment_reminder_service.send_alumni_reminder(
        db, student_id=env["student_id"], sent_by=env["student_id"], today=OVERDUE_DAY
    )
    assert second.sent == []
    assert len(second.skipped_recent) == 1
    assert mail_ok == ["alumni-send@example.com"], "письмо ушло второй раз"


async def test_pointed_reminder_refuses_a_learner_without_debt(db, client):
    """Кнопка по ученику без долга отвечает отказом, а не молчаливым успехом."""
    from tests.test_tsk505_marketer_pricing import _auth

    env = await _setup(db, "tsk805-nodebt", price=550000)
    resp = await client.post(
        f"/api/v1/marketer/payments/alumni-debts/{env['student_id']}/remind",
        headers=_auth(env["token"]),
    )
    assert resp.status_code == 404, resp.text


async def test_alumni_debts_endpoint_shows_the_sum(db, client):
    """Экран маркетолога получает список и общую сумму долга ушедших."""
    from tests.test_tsk505_marketer_pricing import _auth

    env = await _setup(db, "tsk805-api", price=550000)
    await _recalc(db, student_id=env["student_id"])
    await _close_month(db, env["student_id"])
    await _make_alumni(db, env["student_id"])

    resp = await client.get(
        "/api/v1/marketer/payments/alumni-debts", headers=_auth(env["token"])
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    mine = [i for i in body["items"] if i["student_id"] == env["student_id"]]
    assert len(mine) == 1
    assert mine[0]["due_minor"] == 550000
    assert body["total_due_minor"] >= 550000
