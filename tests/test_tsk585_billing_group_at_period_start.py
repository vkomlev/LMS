"""tsk-585: деньги берут группу подписки на ПЕРВОЕ ЧИСЛО расчётного месяца.

Контракт прав §7 разводит две вещи, которые до этой правки в коде были одной:

* **права** — из строки, действующей СЕГОДНЯ (апгрейд включается сразу);
* **деньги** — из строки, действовавшей на первое число расчётного месяца.

Из этой пары само собой выходит решение 14 брифа tsk-301 «права при апгрейде
сразу, деньги со следующего месяца»: отдельного механизма отсрочки нет.

Главный тест здесь — **разница двух случаев**, а не каждый по отдельности:

* `demo` → `base` посреди месяца: на первое число платной группы не было вовсе,
  значит это ПОЯВЛЕНИЕ тарифа — текущий месяц начисляется;
* `base` → `ai` посреди месяца: на первое число уже была платная группа, значит
  это СМЕНА тарифа — текущий месяц не трогается, сумма, названная человеку,
  остаётся прежней.

Схлопнуть их в одно правило «берём действующую подписку» нельзя: именно так код
и работал, и первый же перевод между двумя платными группами посреди месяца
переписал бы открытое начисление.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import charge_service, pricing_service
from tests.test_tsk505_marketer_pricing import _enroll, _new_course, _new_group, _new_user, _price_course

pytestmark = pytest.mark.asyncio

#: Расчётный месяц фиксирован намеренно: тест не должен зависеть от того, какое
#: сегодня число (прогон первого числа иначе ломал бы «посреди месяца»).
PERIOD = date(2026, 9, 1)
#: День смены тарифа — середина расчётного месяца.
MID = date(2026, 9, 15)
#: Дата начала прежней подписки — заведомо раньше первого числа периода.
BEFORE = date(2026, 8, 1)

OLD_PRICE = 300_000
NEW_PRICE = 150_000


async def _subscribe(
    db: AsyncSession,
    *,
    student_id: int,
    plan_code: str,
    group_id: int | None,
    starts_on: date,
    ends_on: date | None = None,
) -> None:
    """Строка истории подписки с явными датами.

    Даты задаём руками: штатный `change_plan` ставит `CURRENT_DATE`, а нам нужен
    именно разрыв «первое число месяца ≠ сегодня».
    """
    await db.execute(
        text(
            "INSERT INTO student_subscription "
            "  (student_id, plan_id, pricing_group_id, starts_on, ends_on) "
            "SELECT :s, id, :g, :from, :to FROM subscription_plan WHERE code = :c"
        ),
        {
            "s": student_id,
            "g": group_id,
            "from": starts_on,
            "to": ends_on,
            "c": plan_code,
        },
    )
    await db.commit()


async def _setup(db: AsyncSession, tag: str) -> dict[str, int]:
    """Ученик на платном курсе прежней группы + вторая группа «нового тарифа».

    Ученик БЕЗ расписания: при нуле ожидаемых занятий доля не применяется и цена
    равна полному тарифу — так сумма в проверках однозначна.
    """
    student_id, _ = await _new_user(db, role="student", name=f"s585-{tag}")
    old_group = await _new_group(db, f"старый-{tag}", [("Общий", OLD_PRICE, None, None)])
    new_group = await _new_group(db, f"новый-{tag}", [("Общий", NEW_PRICE, None, None)])
    course_id = await _new_course(db, f"курс585-{tag}")
    await _price_course(db, course_id=course_id, group_id=old_group)
    await _enroll(db, student_id=student_id, course_id=course_id)
    return {
        "student_id": int(student_id),
        "old_group": int(old_group),
        "new_group": int(new_group),
    }


async def _charges(db: AsyncSession, student_id: int) -> dict[int, int]:
    """Начисления ученика за расчётный месяц: группа → расчётная сумма."""
    rows = (
        await db.execute(
            text(
                "SELECT group_id, calculated_minor, status "
                "  FROM student_monthly_charge "
                " WHERE student_id = :s AND period = :p"
            ),
            {"s": student_id, "p": PERIOD},
        )
    ).all()
    return {int(r.group_id): int(r.calculated_minor) for r in rows}


# ─────────────── Разница двух случаев: появление тарифа vs смена ────────────


async def test_tariff_appearance_mid_month_charges_current_month(
    db: AsyncSession,
) -> None:
    """`demo` → `base` посреди месяца: текущий месяц начисляется.

    На первое число платной группы не было (у demo её нет вовсе), значит человеку
    ещё ничего не называли — начислить текущий месяц нечего переписывать.
    """
    ids = await _setup(db, "appear")
    await _subscribe(
        db, student_id=ids["student_id"], plan_code="demo", group_id=None,
        starts_on=BEFORE, ends_on=MID,
    )
    await _subscribe(
        db, student_id=ids["student_id"], plan_code="base", group_id=ids["new_group"],
        starts_on=MID,
    )

    groups = await pricing_service.billing_group_ids(
        db, student_id=ids["student_id"], period=PERIOD
    )
    assert groups == [ids["new_group"]], (
        "появление платного тарифа обязано начислить текущий месяц"
    )

    await charge_service.recalculate_for_student(
        db, student_id=ids["student_id"], period=PERIOD
    )
    assert await _charges(db, ids["student_id"]) == {ids["new_group"]: NEW_PRICE}


async def test_tariff_change_between_paid_groups_keeps_current_month(
    db: AsyncSession,
) -> None:
    """`base` → `ai` посреди месяца: текущий месяц НЕ трогается.

    Это и есть решение 14: сумма, уже названная человеку за этот месяц, остаётся
    прежней, а новая группа начнёт считаться со следующего.
    """
    ids = await _setup(db, "switch")
    await _subscribe(
        db, student_id=ids["student_id"], plan_code="base", group_id=ids["old_group"],
        starts_on=BEFORE, ends_on=MID,
    )
    await _subscribe(
        db, student_id=ids["student_id"], plan_code="ai", group_id=ids["new_group"],
        starts_on=MID,
    )

    groups = await pricing_service.billing_group_ids(
        db, student_id=ids["student_id"], period=PERIOD
    )
    assert groups == [ids["old_group"]], (
        "смена тарифа посреди месяца переписала группу текущего месяца"
    )

    await charge_service.recalculate_for_student(
        db, student_id=ids["student_id"], period=PERIOD
    )
    assert await _charges(db, ids["student_id"]) == {ids["old_group"]: OLD_PRICE}, (
        "текущий месяц обязан остаться по прежней группе и прежней сумме"
    )


async def test_next_month_takes_the_new_group(db: AsyncSession) -> None:
    """Следующий месяц после смены считается уже по новой группе.

    Без этой проверки правку можно было бы «выполнить» вечной заморозкой старой
    группы: деньги не переписываются никогда, в том числе когда должны.
    """
    ids = await _setup(db, "next")
    await _subscribe(
        db, student_id=ids["student_id"], plan_code="base", group_id=ids["old_group"],
        starts_on=BEFORE, ends_on=MID,
    )
    await _subscribe(
        db, student_id=ids["student_id"], plan_code="ai", group_id=ids["new_group"],
        starts_on=MID,
    )

    groups = await pricing_service.billing_group_ids(
        db, student_id=ids["student_id"], period=charge_service.next_month(PERIOD)
    )
    assert groups == [ids["new_group"]]


# ──────────────────────── Права остаются «сразу» ────────────────────────────


async def test_rights_still_follow_todays_subscription(db: AsyncSession) -> None:
    """Резолвер без периода отдаёт СЕГОДНЯШНЮЮ строку — права не уехали за деньгами.

    Правка касается только денег. Если бы период просочился в резолвер прав,
    апгрейд перестал бы включаться сразу — ровно то, что решение 14 запрещает.
    """
    ids = await _setup(db, "rights")
    await _subscribe(
        db, student_id=ids["student_id"], plan_code="base", group_id=ids["old_group"],
        starts_on=BEFORE, ends_on=MID,
    )
    await _subscribe(
        db, student_id=ids["student_id"], plan_code="ai", group_id=ids["new_group"],
        starts_on=MID,
    )

    today = await pricing_service.active_subscription_groups(db)
    money = await pricing_service.active_subscription_groups(db, period=PERIOD)
    assert today[ids["student_id"]] == ids["new_group"], "права обязаны видеть новый тариф"
    assert money[ids["student_id"]] == ids["old_group"], "деньги обязаны видеть прежний"


# ──────────────── Прежнее поведение там, где подписки нет ───────────────────


async def test_without_subscription_nothing_changes(db: AsyncSession) -> None:
    """Без подписки группа по-прежнему берётся из проданного курса.

    Это свойство делает выкат безопасным: у кого подписки нет, деньги считаются
    ровно как раньше — независимо от того, спрашивают их с периодом или без.
    """
    ids = await _setup(db, "nosub")
    with_period = await pricing_service.billing_group_ids(
        db, student_id=ids["student_id"], period=PERIOD
    )
    without = await pricing_service.billing_group_ids(db, student_id=ids["student_id"])
    assert with_period == without == [ids["old_group"]]


async def test_first_purchase_mid_month_is_charged(db: AsyncSession) -> None:
    """Первая покупка посреди месяца: строки на первое число не было вовсе.

    Отличается от смены тем, что переписывать нечего — начисление создаётся.
    """
    ids = await _setup(db, "first")
    await _subscribe(
        db, student_id=ids["student_id"], plan_code="self", group_id=ids["new_group"],
        starts_on=MID,
    )

    groups = await pricing_service.billing_group_ids(
        db, student_id=ids["student_id"], period=PERIOD
    )
    assert groups == [ids["new_group"]]


async def test_subscription_without_group_still_cancels_money(
    db: AsyncSession,
) -> None:
    """Test/Demo/Выпускник: денег нет вовсе, и период это не меняет.

    Ключ «подписка есть» обязан пережить правку: без него ученик вернулся бы к
    группам из курсов и получил бы начисление, которого быть не должно.
    """
    ids = await _setup(db, "nogroup")
    await _subscribe(
        db, student_id=ids["student_id"], plan_code="alumni", group_id=None,
        starts_on=BEFORE,
    )
    assert await pricing_service.billing_group_ids(
        db, student_id=ids["student_id"], period=PERIOD
    ) == []


# ─────────────────── Закрытый месяц не переписывается ───────────────────────


async def test_closed_month_survives_tariff_change(db: AsyncSession) -> None:
    """Закрытый месяц не трогается при смене тарифа — ни суммой, ни поправкой.

    Инвариант tsk-511 действует поверх новой развилки: даже если бы группа
    разрешилась иначе, история денег остаётся историей.
    """
    ids = await _setup(db, "closed")
    await _subscribe(
        db, student_id=ids["student_id"], plan_code="base", group_id=ids["old_group"],
        starts_on=BEFORE, ends_on=MID,
    )
    await _subscribe(
        db, student_id=ids["student_id"], plan_code="ai", group_id=ids["new_group"],
        starts_on=MID,
    )
    await db.execute(
        text(
            "INSERT INTO student_monthly_charge "
            "  (student_id, group_id, period, calculated_minor, status, closed_at) "
            "VALUES (:s, :g, :p, :c, 'closed', now())"
        ),
        {
            "s": ids["student_id"],
            "g": ids["old_group"],
            "p": PERIOD,
            "c": OLD_PRICE,
        },
    )
    await db.commit()

    await charge_service.recalculate_for_student(
        db, student_id=ids["student_id"], period=PERIOD
    )

    assert await _charges(db, ids["student_id"]) == {ids["old_group"]: OLD_PRICE}
    adjustments = (
        await db.execute(
            text(
                "SELECT count(*) FROM charge_adjustment "
                " WHERE student_id = :s AND origin_period = :p"
            ),
            {"s": ids["student_id"], "p": PERIOD},
        )
    ).scalar_one()
    assert int(adjustments) == 0, "закрытый месяц породил поправку — расчёт поехал"
