"""tsk-744 — отсрочка блокировки за неоплату конкретному ученику.

Смысл рычага: оператор договорился с семьёй подождать до среды и может это
сделать, не сдвигая срок всей школе и не проводя платёж, которого не было.

Главное, что проверяется, — отсрочка НЕ гасит долг. Она откладывает только
закрытие занятий: сумма на экране оплаты остаётся, плашка в кабинете висит.
Иначе «пойти навстречу» означало бы стереть задолженность — ровно та ошибка, по
которой блокировку нельзя делать через `user_courses.is_active`.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.services import (
    payment_access_service,
    payment_block_hold_service,
    payment_service,
)
from app.utils.exceptions import DomainError
from tests.test_tsk511_charges_breaks import _setup, PERIOD
from tests.test_tsk010_payments import _recalc

pytestmark = pytest.mark.asyncio

BLOCK_DAY = payment_service.block_date_for(PERIOD)


async def _make_debtor(db, tag: str) -> int:
    """Ученик, у которого к BLOCK_DAY занятия закрыты за неоплату."""
    env = await _setup(db, tag, price=550000)
    await _recalc(db, student_id=env["student_id"])
    student_id = env["student_id"]
    assert await payment_access_service.has_blocking_debt(
        db, student_id, today=BLOCK_DAY
    ), "предпосылка теста: без отсрочки этот ученик заблокирован"
    return student_id


async def test_hold_postpones_the_block_but_keeps_the_debt(db):
    """Отсрочка открывает занятия и НЕ трогает долг.

    Два утверждения в одном тесте намеренно: порознь они пропустили бы ровно
    ту реализацию, которой опасается задача, — «отложил» через списание долга.
    """
    student_id = await _make_debtor(db, "hold-basic")
    await payment_block_hold_service.create_hold(
        db,
        student_id=student_id,
        until=BLOCK_DAY + timedelta(days=3),
        reason="Родители в отъезде, заплатят в пятницу",
        created_by=student_id,
        today=BLOCK_DAY,
    )

    assert not await payment_access_service.has_blocking_debt(
        db, student_id, today=BLOCK_DAY
    ), "занятия должны открыться"

    # Долг на месте: строка начисления цела, платежей не появилось.
    owed = (
        await db.execute(
            text(
                """
                SELECT COALESCE(c.manual_minor, c.calculated_minor) AS total,
                       (SELECT count(*) FROM student_payment p
                         WHERE p.student_id = c.student_id) AS payments
                  FROM student_monthly_charge c
                 WHERE c.student_id = :s AND c.period = :p
                """
            ),
            {"s": student_id, "p": PERIOD},
        )
    ).one()
    assert owed.total == 550000, "отсрочка списала начисление"
    assert owed.payments == 0, "отсрочка выдумала платёж"

    # И плашка в кабинете продолжает висеть: месяц не оплачен.
    state = payment_service.payment_state(
        total_minor=550000, paid_minor=0, pending_minor=0,
        period=PERIOD, today=BLOCK_DAY,
    )
    assert state.is_due_soon is True and state.is_overdue is True


async def test_hold_expires_by_itself(db):
    """На следующий день после `until` блокировка возвращается сама.

    Ради этого срок и сделан обязательным: забыть про ученика невозможно.
    """
    student_id = await _make_debtor(db, "hold-expiry")
    until = BLOCK_DAY + timedelta(days=2)
    await payment_block_hold_service.create_hold(
        db, student_id=student_id, until=until,
        reason="ждём перевод", created_by=student_id, today=BLOCK_DAY,
    )

    assert not await payment_access_service.has_blocking_debt(
        db, student_id, today=until
    ), "в последний день отсрочки занятия ещё открыты"
    assert await payment_access_service.has_blocking_debt(
        db, student_id, today=until + timedelta(days=1)
    ), "назавтра отсрочка обязана истечь"


async def test_cancelling_a_hold_returns_the_block(db):
    """Снятая досрочно отсрочка не действует, но остаётся в истории."""
    student_id = await _make_debtor(db, "hold-cancel")
    hold = await payment_block_hold_service.create_hold(
        db, student_id=student_id, until=BLOCK_DAY + timedelta(days=10),
        reason="обещали в среду", created_by=student_id, today=BLOCK_DAY,
    )

    cancelled = await payment_block_hold_service.cancel_hold(
        db, hold_id=hold.id, cancelled_by=student_id
    )
    assert cancelled is not None and cancelled.is_active is False
    assert await payment_access_service.has_blocking_debt(
        db, student_id, today=BLOCK_DAY
    ), "после снятия блокировка обязана вернуться"

    history = await payment_block_hold_service.list_holds(
        db, student_id=student_id, only_active=False
    )
    assert len(history) == 1, "строка должна остаться в истории, а не исчезнуть"

    # Повторное снятие — не событие, а ничего.
    assert await payment_block_hold_service.cancel_hold(
        db, hold_id=hold.id, cancelled_by=student_id
    ) is None


async def test_new_hold_replaces_the_previous_one(db):
    """Новая отсрочка снимает прежнюю — иначе срок нельзя было бы сократить.

    Без этого «передумал, откладываю только до завтра» не работало бы: старая,
    более дальняя отсрочка продолжала бы действовать молча.
    """
    student_id = await _make_debtor(db, "hold-replace")
    await payment_block_hold_service.create_hold(
        db, student_id=student_id, until=BLOCK_DAY + timedelta(days=20),
        reason="сначала отложили далеко", created_by=student_id, today=BLOCK_DAY,
    )
    await payment_block_hold_service.create_hold(
        db, student_id=student_id, until=BLOCK_DAY + timedelta(days=2),
        reason="передумали, только до среды", created_by=student_id, today=BLOCK_DAY,
    )

    active = await payment_block_hold_service.list_holds(
        db, student_id=student_id, only_active=True, today=BLOCK_DAY
    )
    assert len(active) == 1, "действующей должна остаться одна"
    assert active[0].until == BLOCK_DAY + timedelta(days=2)

    assert await payment_access_service.has_blocking_debt(
        db, student_id, today=BLOCK_DAY + timedelta(days=5)
    ), "короткий срок обязан пересилить прежний длинный"


async def test_hold_refuses_past_date_empty_reason_and_too_far(db):
    """Три отказа, каждый — про свою ошибку оператора."""
    student_id = await _make_debtor(db, "hold-validation")
    today = BLOCK_DAY

    with pytest.raises(DomainError):
        await payment_block_hold_service.create_hold(
            db, student_id=student_id, until=today - timedelta(days=1),
            reason="вчера", created_by=student_id, today=today,
        )

    with pytest.raises(DomainError):
        await payment_block_hold_service.create_hold(
            db, student_id=student_id, until=today + timedelta(days=3),
            reason="   ", created_by=student_id, today=today,
        )

    with pytest.raises(DomainError):
        await payment_block_hold_service.create_hold(
            db, student_id=student_id,
            until=today + timedelta(days=payment_block_hold_service.MAX_HOLD_DAYS + 1),
            reason="почти навсегда", created_by=student_id, today=today,
        )

    # Ни одна из трёх попыток не должна была оставить следа.
    assert await payment_access_service.has_blocking_debt(db, student_id, today=today)
    assert await payment_block_hold_service.list_holds(
        db, student_id=student_id, only_active=False
    ) == []


async def test_hold_touches_only_its_own_student(db):
    """Отсрочка одному не открывает занятия другому должнику."""
    lucky = await _make_debtor(db, "hold-lucky")
    other = await _make_debtor(db, "hold-other")
    await payment_block_hold_service.create_hold(
        db, student_id=lucky, until=BLOCK_DAY + timedelta(days=5),
        reason="договорились", created_by=lucky, today=BLOCK_DAY,
    )

    assert not await payment_access_service.has_blocking_debt(
        db, lucky, today=BLOCK_DAY
    )
    assert await payment_access_service.has_blocking_debt(
        db, other, today=BLOCK_DAY
    ), "сосед по долгу не должен был ничего получить"
