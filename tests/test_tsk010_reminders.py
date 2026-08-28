"""tsk-010 — напоминания о просроченной оплате.

Проверяем деликатность и честность рассылки: письмо не уходит дважды за неделю,
человек без почты не проглатывается, сбой почты не закрывает ему напоминание на
неделю вперёд, а тому, у кого срок ещё не вышел, не пишут вовсе.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.services import (
    notification_email_service,
    payment_reminder_service,
    payment_service,
)
from tests.test_tsk505_marketer_pricing import _auth
from tests.test_tsk511_charges_breaks import _setup, PERIOD
from tests.test_tsk010_payments import _charge_id, _login_as, _recalc, _submit

pytestmark = pytest.mark.asyncio

#: Первый день просрочки: месяц оплачивается до своего конца, поэтому письмо
#: уходит уже со следующего дня.
OVERDUE_DAY = payment_service.due_date_for(PERIOD) + timedelta(days=1)


@pytest.fixture
def mail_ok(monkeypatch):
    """Почта «работает»: запоминаем адресатов, наружу ничего не уходит."""
    sent: list[str] = []

    async def fake_send(**kwargs) -> bool:
        sent.append(kwargs["recipient_email"])
        return True

    monkeypatch.setattr(notification_email_service, "send_payment_overdue", fake_send)
    return sent


async def _set_email(db, user_id: int, email: str | None) -> None:
    await db.execute(
        text("UPDATE users SET email = :e WHERE id = :id"), {"e": email, "id": user_id}
    )
    await db.commit()


async def test_reminder_goes_once_a_week(db, client, mail_ok, monkeypatch):
    """Второе нажатие кнопки в тот же день не отправляет письмо повторно."""
    env = await _setup(db, "rem-once", price=550000)
    await _recalc(db, student_id=env["student_id"])
    await _set_email(db, env["student_id"], "debtor-once@example.com")

    first = await payment_reminder_service.send_reminders(
        db, sent_by=env["student_id"], today=OVERDUE_DAY
    )
    assert len(first.sent) == 1, first
    assert mail_ok == ["debtor-once@example.com"]

    second = await payment_reminder_service.send_reminders(
        db, sent_by=env["student_id"], today=OVERDUE_DAY
    )
    assert second.sent == []
    assert len(second.skipped_recent) == 1
    assert mail_ok == ["debtor-once@example.com"], "письмо ушло второй раз"

    # А через неделю — можно снова. Двигаем не «сегодня», а само время записи в
    # журнале: окно повтора считается от реального времени отправки, и подмена
    # даты «сегодня» его не касается.
    await db.execute(
        text(
            "UPDATE notifications SET modified_at = now() - interval '8 days' "
            "WHERE kind = :k AND user_id = :u"
        ),
        {"k": payment_reminder_service.REMINDER_KIND, "u": env["student_id"]},
    )
    await db.commit()

    later = await payment_reminder_service.send_reminders(
        db, sent_by=env["student_id"], today=OVERDUE_DAY
    )
    assert len(later.sent) == 1
    assert len(mail_ok) == 2


async def test_student_without_email_is_named_not_swallowed(db, client, mail_ok):
    """Ученику без почты письмо не уходит, но его называют поимённо."""
    env = await _setup(db, "rem-noemail", price=550000)
    await _recalc(db, student_id=env["student_id"])
    await _set_email(db, env["student_id"], None)

    run = await payment_reminder_service.send_reminders(
        db, sent_by=env["student_id"], today=OVERDUE_DAY
    )
    assert run.sent == []
    assert len(run.without_email) == 1, "человек, которому некуда писать, пропал из отчёта"
    assert mail_ok == []


async def test_mail_failure_does_not_block_next_attempt(db, client, monkeypatch):
    """Сбой почты не закрывает напоминание на неделю: записи в журнал нет."""
    env = await _setup(db, "rem-fail", price=550000)
    await _recalc(db, student_id=env["student_id"])
    await _set_email(db, env["student_id"], "broken@example.com")

    async def failing_send(**kwargs) -> bool:
        return False

    monkeypatch.setattr(notification_email_service, "send_payment_overdue", failing_send)
    failed = await payment_reminder_service.send_reminders(
        db, sent_by=env["student_id"], today=OVERDUE_DAY
    )
    assert len(failed.failed) == 1
    assert failed.sent == []

    journal = (
        await db.execute(
            text("SELECT count(*) AS n FROM notifications WHERE kind = :k AND user_id = :u"),
            {"k": payment_reminder_service.REMINDER_KIND, "u": env["student_id"]},
        )
    ).one()
    assert journal.n == 0, "неудачная отправка попала в журнал и закрыла повтор"

    # Почта починилась — письмо уходит сразу, а не через неделю.
    sent: list[str] = []

    async def working_send(**kwargs) -> bool:
        sent.append(kwargs["recipient_email"])
        return True

    monkeypatch.setattr(notification_email_service, "send_payment_overdue", working_send)
    retry = await payment_reminder_service.send_reminders(
        db, sent_by=env["student_id"], today=OVERDUE_DAY
    )
    assert len(retry.sent) == 1
    assert sent == ["broken@example.com"]


async def test_not_overdue_yet_gets_no_letter(db, client, mail_ok):
    """Пока срок с запасом не вышел — не пишем."""
    env = await _setup(db, "rem-early", price=550000)
    await _recalc(db, student_id=env["student_id"])
    await _set_email(db, env["student_id"], "early@example.com")

    # В последний день месяца человек ещё не должник — письма быть не должно.
    run = await payment_reminder_service.send_reminders(
        db, sent_by=env["student_id"], today=payment_service.due_date_for(PERIOD)
    )
    assert run.sent == []
    assert mail_ok == []


async def test_paid_student_is_not_reminded(db, client, mail_ok):
    """Оплатившему не напоминают — даже если срок давно прошёл."""
    env = await _setup(db, "rem-paid", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    await _set_email(db, env["student_id"], "paid@example.com")
    _, token = await _login_as(db, env["student_id"])

    resp = await _submit(client, token, charge_id=charge_id, amount_minor=550000)
    await client.post(
        f"/api/v1/marketer/payments/{resp.json()['id']}/confirm",
        json={},
        headers=_auth(env["token"]),
    )

    run = await payment_reminder_service.send_reminders(
        db, sent_by=env["student_id"], today=OVERDUE_DAY
    )
    assert run.sent == []
    assert mail_ok == []


async def test_pending_receipt_holds_the_letter(db, client, mail_ok):
    """Приложил чек, ждёт подтверждения — письмо о долге не шлём.

    Иначе человек, честно оплативший вчера, получает укор за нашу же очередь.
    """
    env = await _setup(db, "rem-pending", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    await _set_email(db, env["student_id"], "pending@example.com")
    _, token = await _login_as(db, env["student_id"])

    await _submit(client, token, charge_id=charge_id, amount_minor=550000)

    run = await payment_reminder_service.send_reminders(
        db, sent_by=env["student_id"], today=OVERDUE_DAY
    )
    assert run.sent == []
    assert mail_ok == []


async def test_preview_is_closed_for_students(db, client):
    """Список должников — не для учеников."""
    env = await _setup(db, "rem-gate", price=550000)
    _, token = await _login_as(db, env["student_id"])

    resp = await client.get("/api/v1/marketer/payments/reminders", headers=_auth(token))
    assert resp.status_code == 403


async def test_preview_splits_recipients(db, client, mail_ok):
    """Предпросмотр честно делит: кому уйдёт, кому некуда."""
    with_mail = await _setup(db, "rem-prev-a", price=550000)
    no_mail = await _setup(db, "rem-prev-b", price=550000)
    await _recalc(db, student_id=with_mail["student_id"])
    await _recalc(db, student_id=no_mail["student_id"])
    await _set_email(db, with_mail["student_id"], "prev@example.com")
    await _set_email(db, no_mail["student_id"], None)

    resp = await client.get(
        "/api/v1/marketer/payments/reminders", headers=_auth(with_mail["token"])
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids_send = {d["student_id"] for d in body["will_send"]}
    ids_none = {d["student_id"] for d in body["without_email"]}
    # Предпросмотр смотрит на сегодня: тестовый период ещё не просрочен,
    # поэтому здесь важно, что списки не путают людей местами.
    assert with_mail["student_id"] not in ids_none
    assert no_mail["student_id"] not in ids_send
    # Почтовый адрес наружу не отдаётся — только признак.
    for row in body["will_send"] + body["without_email"]:
        assert "email" not in row
        assert "has_email" in row
