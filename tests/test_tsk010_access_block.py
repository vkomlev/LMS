"""tsk-010 — мягкая блокировка учёбы за неоплату.

Главное, что проверяем: закрыты материалы и задания, но НЕ закрыта дорога к
оплате. Иначе человек не увидит долг и не сможет заплатить — блокировка станет
ловушкой вместо напоминания.

Ещё: блокировка снимается сама при оплате, приложенный чек её придерживает, а
преподавателя долг ученика не касается.

tsk-617: отказ несёт машинный признак и дорогу к деньгам (`payload.code`, сумма,
месяцы, ссылка на кабинет), а сервисный ключ от проверки больше не освобождает —
иначе блокировка снималась бы сменой клиента на Telegram-бота.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.services import payment_access_service, payment_service
from tests.test_tsk505_marketer_pricing import _auth, _new_user
from tests.test_tsk511_charges_breaks import _setup, PERIOD
from tests.test_tsk010_payments import _charge_id, _login_as, _recalc, _submit

pytestmark = pytest.mark.asyncio

_settings = Settings()


def _service_headers() -> dict[str, str]:
    """Заголовок бота: тот же сервисный ключ, которым ходит TG_LMS."""
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}

#: День, когда занятия уже закрыты: месяц оплачивается до своего конца, потом
#: несколько дней на оплату «вдогонку». Для сентябрьского PERIOD это 5 октября.
BLOCK_DAY = payment_service.block_date_for(PERIOD)

#: Первый день просрочки — сразу после конца месяца. Пометка и письмо есть,
#: занятия ещё открыты.
FIRST_OVERDUE_DAY = payment_service.due_date_for(PERIOD) + timedelta(days=1)


async def _make_debtor(db, tag: str):
    """Ученик с начислением, по которому срок ещё не вышел."""
    env = await _setup(db, tag, price=550000)
    await _recalc(db, student_id=env["student_id"])
    return env


async def test_unpaid_month_in_progress_does_not_block(db):
    """Пока месяц идёт, неоплата не закрывает учёбу.

    Главная защита от ошибки, которую поймал оператор: за август платят ДО
    КОНЦА августа, и 12-го числа человек ещё не должник.
    """
    env = await _make_debtor(db, "blk-fresh")
    mid_month = PERIOD + timedelta(days=11)
    assert not await payment_access_service.has_blocking_debt(
        db, env["student_id"], today=mid_month
    )
    # И в последний день месяца — тоже ещё не должник.
    assert not await payment_access_service.has_blocking_debt(
        db, env["student_id"], today=payment_service.due_date_for(PERIOD)
    )


async def test_overdue_marked_but_not_yet_blocked(db):
    """Между пометкой «просрочено» и закрытием занятий есть несколько дней."""
    env = await _make_debtor(db, "blk-grace")
    state = payment_service.payment_state(
        total_minor=550000, paid_minor=0, pending_minor=0,
        period=PERIOD, today=FIRST_OVERDUE_DAY,
    )
    assert state.is_overdue is True, "месяц кончился — пометка должна быть"
    assert state.is_blocked is False, "занятия закрылись в первый же день просрочки"

    assert not await payment_access_service.has_blocking_debt(
        db, env["student_id"], today=FIRST_OVERDUE_DAY
    )


async def test_block_starts_on_its_own_day(db):
    """С назначенного дня следующего месяца — блокировка."""
    env = await _make_debtor(db, "blk-overdue")
    blocked = await payment_access_service.has_blocking_debt(
        db, env["student_id"], today=BLOCK_DAY
    )
    assert blocked is True


async def test_pending_receipt_holds_the_block(db, client):
    """Приложил чек — занятия не закрываются, пока мы его смотрим.

    Человек своё сделал; закрывать ему учёбу за нашу очередь нельзя.
    """
    env = await _make_debtor(db, "blk-pending")
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, token = await _login_as(db, env["student_id"])
    await _submit(client, token, charge_id=charge_id, amount_minor=550000)

    blocked = await payment_access_service.has_blocking_debt(
        db, env["student_id"], today=BLOCK_DAY
    )
    assert blocked is False


async def test_payment_lifts_the_block(db, client):
    """Оплата снимает блокировку сразу — без крона и ручного действия."""
    env = await _make_debtor(db, "blk-paid")
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, token = await _login_as(db, env["student_id"])

    assert await payment_access_service.has_blocking_debt(
        db, env["student_id"], today=BLOCK_DAY
    )

    resp = await _submit(client, token, charge_id=charge_id, amount_minor=550000)
    await client.post(
        f"/api/v1/marketer/payments/{resp.json()['id']}/confirm",
        json={},
        headers=_auth(env["token"]),
    )

    assert not await payment_access_service.has_blocking_debt(
        db, env["student_id"], today=BLOCK_DAY
    )


async def test_blocked_student_still_sees_charges_and_can_pay(db, client, monkeypatch):
    """Дорога к оплате открыта: долг видно, чек приложить можно.

    Это главное свойство мягкой блокировки — иначе она превращается в ловушку.
    """
    env = await _make_debtor(db, "blk-road")
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, token = await _login_as(db, env["student_id"])

    # Двигаем «сегодня» в блокирующий день для самого гейта.
    monkeypatch.setattr(payment_access_service, "blocking_debt", _always_blocked)

    charges = await client.get("/api/v1/me/charges", headers=_auth(token))
    assert charges.status_code == 200, "кабинет оплаты закрылся вместе с учёбой"
    assert charges.json()[0]["due_minor"] > 0

    paid = await _submit(client, token, charge_id=charge_id, amount_minor=550000)
    assert paid.status_code == 201, "заблокированный не смог приложить чек"


async def _always_blocked(db, student_id, *, today=None):
    """Долг, закрывающий занятия: 5 500 ₽ за сентябрь (PERIOD тестов)."""
    return payment_access_service.BlockingDebt(due_minor=550000, periods=(PERIOD,))


async def test_blocked_student_cannot_open_task(db, client, monkeypatch):
    """Задание заблокированному не открывается, и текст объясняет почему."""
    env = await _make_debtor(db, "blk-task")
    _, token = await _login_as(db, env["student_id"])
    monkeypatch.setattr(payment_access_service, "blocking_debt", _always_blocked)

    task_id = (
        await db.execute(
            text(
                "INSERT INTO tasks (course_id, task_content, difficulty_id, external_uid) "
                "VALUES (:c, '{\"stem\": \"blk\"}'::jsonb, 1, 'blk-uid-010') RETURNING id"
            ),
            {"c": env["course_id"]},
        )
    ).scalar()
    await db.commit()

    resp = await client.get(f"/api/v1/tasks/{task_id}", headers=_auth(token))
    assert resp.status_code == 403
    assert "неоплат" in resp.json()["detail"].lower()


async def test_teacher_is_not_affected_by_student_debt(db, client, monkeypatch):
    """Долг ученика не закрывает материалы преподавателю."""
    env = await _make_debtor(db, "blk-teacher")
    _, teacher_token = await _new_user(db, role="teacher", name="blk-teacher-010")
    monkeypatch.setattr(payment_access_service, "blocking_debt", _always_blocked)

    task_id = (
        await db.execute(
            text(
                "INSERT INTO tasks (course_id, task_content, difficulty_id, external_uid) "
                "VALUES (:c, '{\"stem\": \"blk-t\"}'::jsonb, 1, 'blk-uid-010-t') RETURNING id"
            ),
            {"c": env["course_id"]},
        )
    ).scalar()
    await db.commit()

    resp = await client.get(f"/api/v1/tasks/{task_id}", headers=_auth(teacher_token))
    assert resp.status_code == 200, "преподаватель пострадал от чужого долга"


async def test_denial_carries_machine_code_and_the_way_to_pay(db, client, monkeypatch):
    """Тело отказа объясняет отказ машинно и ведёт к деньгам (tsk-617).

    Без `payload.code` клиенту остаётся разбирать текст, а он меняется вместе с
    ценами: разбор сломался бы молча, и ученик снова увидел бы «недостаточно
    прав». Без ссылки на кабинет отказ — тупик: в Telegram-боте раздела
    «Оплата» нет.
    """
    env = await _make_debtor(db, "blk-body")
    _, token = await _login_as(db, env["student_id"])
    monkeypatch.setattr(payment_access_service, "blocking_debt", _always_blocked)

    resp = await client.get(
        f"/api/v1/learning/next-item?student_id={env['student_id']}",
        headers=_auth(token),
    )
    assert resp.status_code == 403
    body = resp.json()
    payload = body["payload"]
    assert payload["code"] == payment_access_service.PAYMENT_OVERDUE_CODE
    assert payload["due_minor"] == 550000
    assert payload["periods"] == [PERIOD.isoformat()]
    assert payload["payments_url"].endswith("/me/payments")
    # Сумма и месяц — в самом тексте: «оплатите» без числа заставляет искать
    # долг там, где как раз всё закрыто.
    assert "5500 ₽" in body["detail"]
    assert f"{PERIOD:%m.%Y}" in body["detail"]


async def test_service_key_does_not_lift_the_block(db, client, monkeypatch):
    """Бот не снимает блокировку: гейт про ученика, а не про клиента (tsk-617).

    До этой правки сервисный ключ освобождал от проверки целиком — должник,
    закрытый в браузере, спокойно учился через Telegram-бота.
    """
    env = await _make_debtor(db, "blk-service")
    monkeypatch.setattr(payment_access_service, "blocking_debt", _always_blocked)

    resp = await client.get(
        f"/api/v1/learning/next-item?student_id={env['student_id']}",
        headers=_service_headers(),
    )
    assert resp.status_code == 403, "бот прошёл мимо блокировки за неоплату"
    assert resp.json()["payload"]["code"] == payment_access_service.PAYMENT_OVERDUE_CODE


async def test_debtor_still_reads_his_help_request_state(db, client, monkeypatch):
    """Состояние собственной заявки должнику видно и через бота.

    Закрыт учебный контент, а не поддержка: иначе человек, уже написавший
    преподавателю, потерял бы и ответ на своё обращение.
    """
    env = await _make_debtor(db, "blk-ladder")
    monkeypatch.setattr(payment_access_service, "blocking_debt", _always_blocked)

    task_id = (
        await db.execute(
            text(
                "INSERT INTO tasks (course_id, task_content, difficulty_id, external_uid) "
                "VALUES (:c, '{\"stem\": \"blk-hr\"}'::jsonb, 1, 'blk-uid-010-hr') RETURNING id"
            ),
            {"c": env["course_id"]},
        )
    ).scalar()
    await db.commit()

    resp = await client.get(
        f"/api/v1/learning/tasks/{task_id}/help-request?student_id={env['student_id']}",
        headers=_service_headers(),
    )
    assert resp.status_code == 200


async def test_message_falls_back_when_months_are_unknown():
    """Пустой долг без месяцев — текст остаётся человеческим, а не пустым."""
    empty = payment_access_service.BlockingDebt(due_minor=0, periods=())
    assert payment_access_service.blocked_message(empty) == (
        payment_access_service.BLOCKED_MESSAGE
    )


async def test_message_shows_kopecks_only_when_they_exist():
    """Круглая сумма — без копеек; иначе «5500.00 ₽» читается как ошибка."""
    round_debt = payment_access_service.BlockingDebt(due_minor=550000, periods=(PERIOD,))
    odd_debt = payment_access_service.BlockingDebt(due_minor=550050, periods=(PERIOD,))
    assert "5500 ₽" in payment_access_service.blocked_message(round_debt)
    assert "5500.50 ₽" in payment_access_service.blocked_message(odd_debt)


async def test_block_does_not_erase_the_debt(db):
    """Блокировка не трогает зачисление — иначе исчез бы сам долг.

    Ровно этот сценарий и запрещал делать её через user_courses.is_active.
    """
    env = await _make_debtor(db, "blk-keeps-debt")
    await payment_access_service.has_blocking_debt(db, env["student_id"], today=BLOCK_DAY)

    row = (
        await db.execute(
            text("SELECT is_active FROM user_courses WHERE user_id = :u AND course_id = :c"),
            {"u": env["student_id"], "c": env["course_id"]},
        )
    ).one()
    assert row.is_active is True

    charge = (
        await db.execute(
            text(
                "SELECT count(*) AS n FROM student_monthly_charge "
                "WHERE student_id = :s AND status = 'open'"
            ),
            {"s": env["student_id"]},
        )
    ).one()
    assert charge.n > 0, "начисление пропало — долг стёрся вместе с доступом"
