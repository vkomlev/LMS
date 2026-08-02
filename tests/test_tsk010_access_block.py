"""tsk-010 — мягкая блокировка учёбы за неоплату.

Главное, что проверяем: закрыты материалы и задания, но НЕ закрыта дорога к
оплате. Иначе человек не увидит долг и не сможет заплатить — блокировка станет
ловушкой вместо напоминания.

Ещё: блокировка снимается сама при оплате, приложенный чек её придерживает, а
преподавателя долг ученика не касается.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.services import payment_access_service, payment_service
from tests.test_tsk505_marketer_pricing import _auth, _new_user
from tests.test_tsk511_charges_breaks import _setup, PERIOD
from tests.test_tsk010_payments import _charge_id, _login_as, _recalc, _submit

pytestmark = pytest.mark.asyncio

#: День, когда просрочка уже наступила (5-е число + 7 дней запаса).
OVERDUE_DAY = PERIOD + timedelta(days=20)


async def _make_debtor(db, tag: str):
    """Ученик с начислением, по которому срок ещё не вышел."""
    env = await _setup(db, tag, price=550000)
    await _recalc(db, student_id=env["student_id"])
    return env


async def test_debt_alone_does_not_block(db):
    """Долг сам по себе не закрывает учёбу — только просроченный."""
    env = await _make_debtor(db, "blk-fresh")
    blocked = await payment_access_service.has_overdue_debt(
        db, env["student_id"], today=payment_service.due_date_for(PERIOD)
    )
    assert blocked is False


async def test_overdue_blocks_content(db):
    """После срока с запасом — блокировка."""
    env = await _make_debtor(db, "blk-overdue")
    blocked = await payment_access_service.has_overdue_debt(
        db, env["student_id"], today=OVERDUE_DAY
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

    blocked = await payment_access_service.has_overdue_debt(
        db, env["student_id"], today=OVERDUE_DAY
    )
    assert blocked is False


async def test_payment_lifts_the_block(db, client):
    """Оплата снимает блокировку сразу — без крона и ручного действия."""
    env = await _make_debtor(db, "blk-paid")
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, token = await _login_as(db, env["student_id"])

    assert await payment_access_service.has_overdue_debt(
        db, env["student_id"], today=OVERDUE_DAY
    )

    resp = await _submit(client, token, charge_id=charge_id, amount_minor=550000)
    await client.post(
        f"/api/v1/marketer/payments/{resp.json()['id']}/confirm",
        json={},
        headers=_auth(env["token"]),
    )

    assert not await payment_access_service.has_overdue_debt(
        db, env["student_id"], today=OVERDUE_DAY
    )


async def test_blocked_student_still_sees_charges_and_can_pay(db, client, monkeypatch):
    """Дорога к оплате открыта: долг видно, чек приложить можно.

    Это главное свойство мягкой блокировки — иначе она превращается в ловушку.
    """
    env = await _make_debtor(db, "blk-road")
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, token = await _login_as(db, env["student_id"])

    # Двигаем «сегодня» в блокирующий день для самого гейта.
    monkeypatch.setattr(
        payment_access_service, "has_overdue_debt", _always_blocked
    )

    charges = await client.get("/api/v1/me/charges", headers=_auth(token))
    assert charges.status_code == 200, "кабинет оплаты закрылся вместе с учёбой"
    assert charges.json()[0]["due_minor"] > 0

    paid = await _submit(client, token, charge_id=charge_id, amount_minor=550000)
    assert paid.status_code == 201, "заблокированный не смог приложить чек"


async def _always_blocked(db, student_id, *, today=None) -> bool:
    return True


async def test_blocked_student_cannot_open_task(db, client, monkeypatch):
    """Задание заблокированному не открывается, и текст объясняет почему."""
    env = await _make_debtor(db, "blk-task")
    _, token = await _login_as(db, env["student_id"])
    monkeypatch.setattr(payment_access_service, "has_overdue_debt", _always_blocked)

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
    monkeypatch.setattr(payment_access_service, "has_overdue_debt", _always_blocked)

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


async def test_block_does_not_erase_the_debt(db):
    """Блокировка не трогает зачисление — иначе исчез бы сам долг.

    Ровно этот сценарий и запрещал делать её через user_courses.is_active.
    """
    env = await _make_debtor(db, "blk-keeps-debt")
    await payment_access_service.has_overdue_debt(db, env["student_id"], today=OVERDUE_DAY)

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
