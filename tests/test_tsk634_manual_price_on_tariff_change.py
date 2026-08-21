"""tsk-634: смена тарифа не должна молча трогать ручные деньги.

Два вида «ручной цены» разные, и ведут себя по-разному. Обе привязаны к
ТАРИФНОЙ ГРУППЕ, а договорённость с человеком привязана к человеку — отсюда
весь дефект:

* `student_monthly_charge.manual_minor` — сумма КОНКРЕТНОГО месяца, живёт на
  строке с ключом (ученик, группа, период). Смена тарифа меняет группу, строка
  прежней группы удаляется как «считать не из чего» — и ручная сумма исчезает
  вместе с ней, без единой записи в журнал;
* `student_price_override.price_minor` — цена ученика В ГРУППЕ, бессрочная,
  ключ (ученик, группа). Смена тарифа её НЕ удаляет — она осиротевает: к новой
  группе не применяется, а строку старой продолжает «оживлять», из-за чего за
  один месяц выходит ДВА начисления.

Тесты идут от денежного исхода («сколько человек должен за месяц»), а не от
внутренностей: строка вправе переехать в другую группу, важно, что сумма месяца
не выросла молча.

Форма фикстуры повторяет прод: ученик ведётся ПОДПИСКОЙ (так с tsk-301 живут все
57 человек), группы свои, тариф плоский — доля неполного месяца здесь ни при чём.
"""
from __future__ import annotations

import random
import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.subscription import ManualPricingState
from app.services import charge_service, subscription_service

pytestmark = pytest.mark.asyncio

#: Месяц текущий: резолвер денег смотрит на подписку, действовавшую на первое
#: число, а тестовые подписки заводятся сегодняшним днём.
PERIOD = date.today().replace(day=1)

OLD_PRICE = 550_000
NEW_PRICE = 700_000
MANUAL = 275_000


async def _new_group(db: AsyncSession, name: str, price: int) -> int:
    """Тарифная группа с единственным плоским вариантом."""
    group_id = (
        await db.execute(
            text("INSERT INTO pricing_group (name) VALUES (:n) RETURNING id"),
            {"n": f"tsk634-{name}-{random.randint(10**6, 10**7)}"},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO pricing_tariff "
            "  (group_id, name, price_minor, match_kind, match_value, sort_order) "
            "VALUES (:g, 'Общий', :p, NULL, NULL, 0)"
        ),
        {"g": group_id, "p": price},
    )
    await db.commit()
    return int(group_id)


@pytest_asyncio.fixture(scope="function")
async def two_tariffs(db: AsyncSession) -> dict[str, int]:
    """Ученик на группе «старый тариф» и вторая группа для перевода."""
    student_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO users (full_name, email, is_active) "
                    "VALUES ('tsk634 ученик', :e, true) RETURNING id"
                ),
                {"e": f"tsk634-{uuid.uuid4().hex[:12]}@example.test"},
            )
        ).scalar_one()
    )
    old_group = await _new_group(db, "staryj", OLD_PRICE)
    new_group = await _new_group(db, "novyj", NEW_PRICE)
    await _subscribe(db, student_id, "base", old_group)
    return {
        "student_id": student_id,
        "old_group": old_group,
        "new_group": new_group,
    }


async def _subscribe(
    db: AsyncSession, student_id: int, plan_code: str, group_id: int | None
) -> None:
    """Присвоить тариф так же, как штатная смена: закрыть строку, открыть новую."""
    await db.execute(
        text(
            "UPDATE student_subscription SET ends_on = CURRENT_DATE "
            " WHERE student_id = :s AND ends_on IS NULL"
        ),
        {"s": student_id},
    )
    await db.execute(
        text(
            "INSERT INTO student_subscription "
            "  (student_id, plan_id, pricing_group_id, starts_on) "
            "SELECT :s, id, :g, CURRENT_DATE FROM subscription_plan WHERE code = :c"
        ),
        {"s": student_id, "g": group_id, "c": plan_code},
    )
    await db.commit()


async def _charges(db: AsyncSession, student_id: int) -> list[dict]:
    rows = (
        await db.execute(
            text(
                "SELECT id, group_id, calculated_minor, manual_minor, status "
                "  FROM student_monthly_charge "
                " WHERE student_id = :s AND period = :p ORDER BY group_id"
            ),
            {"s": student_id, "p": PERIOD},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


def _total(rows: list[dict]) -> int:
    """Сколько человек должен за месяц по ВСЕМ строкам сразу."""
    return sum(
        charge_service.charge_total_minor(
            calculated_minor=r["calculated_minor"],
            manual_minor=r["manual_minor"],
            adjustments_minor=0,
        )
        for r in rows
    )


# ───────────────── 1. Ручная сумма месяца переживает смену тарифа ───────────


async def test_manual_month_amount_survives_tariff_change(
    db: AsyncSession, two_tariffs: dict[str, int]
) -> None:
    """Случай Терехова (4510): 2 750 ₽ руками, потом штатный перевод на тариф.

    Сумма месяца не должна вырасти от технической операции — человеку её уже
    назвали. Проверяем денежный исход, а не то, на какой группе живёт строка.
    """
    student_id = two_tariffs["student_id"]
    await charge_service.recalculate_for_student(db, student_id=student_id, period=PERIOD)
    before = await _charges(db, student_id)
    assert len(before) == 1, "до перевода строка ровно одна"
    assert _total(before) == OLD_PRICE

    await charge_service.set_manual_amount(
        db, charge_id=before[0]["id"], amount_minor=MANUAL
    )

    await _subscribe(db, student_id, "self", two_tariffs["new_group"])
    await charge_service.recalculate_for_student(db, student_id=student_id, period=PERIOD)

    after = await _charges(db, student_id)
    assert len(after) == 1, f"перевод не должен плодить второе начисление: {after}"
    assert _total(after) == MANUAL, (
        "ручная сумма месяца потеряна при смене тарифа: человеку обещали "
        f"{MANUAL / 100:.2f} ₽, а вышло {_total(after) / 100:.2f} ₽"
    )


async def test_manual_amount_is_carried_with_a_trace(
    db: AsyncSession, two_tariffs: dict[str, int]
) -> None:
    """Перенос ручной суммы виден в базе: он на новой строке, а не на старой.

    Отдельно от денежного исхода: сумма должна оказаться именно на строке
    действующей группы, иначе экран начислений покажет её в покинутом тарифе.
    """
    student_id = two_tariffs["student_id"]
    await charge_service.recalculate_for_student(db, student_id=student_id, period=PERIOD)
    rows = await _charges(db, student_id)
    await charge_service.set_manual_amount(db, charge_id=rows[0]["id"], amount_minor=MANUAL)

    await _subscribe(db, student_id, "self", two_tariffs["new_group"])
    await charge_service.recalculate_for_student(db, student_id=student_id, period=PERIOD)

    after = await _charges(db, student_id)
    assert len(after) == 1
    assert after[0]["group_id"] == two_tariffs["new_group"], "строка обязана переехать"
    assert after[0]["manual_minor"] == MANUAL
    assert after[0]["calculated_minor"] == NEW_PRICE, (
        "расчёт по новому тарифу должен быть виден рядом — иначе на экране не "
        "объяснить, почему сумма ниже тарифа"
    )


async def test_clear_manual_still_returns_to_calculation(
    db: AsyncSession, two_tariffs: dict[str, int]
) -> None:
    """Обратный путь «Вернуть к расчёту» обязан снимать ручную сумму намеренно.

    Сохранение суммы при переводе не должно превратить её в несмываемую: кнопка
    на экране начислений — единственный способ от неё отказаться.
    """
    student_id = two_tariffs["student_id"]
    await charge_service.recalculate_for_student(db, student_id=student_id, period=PERIOD)
    rows = await _charges(db, student_id)
    await charge_service.set_manual_amount(db, charge_id=rows[0]["id"], amount_minor=MANUAL)

    await _subscribe(db, student_id, "self", two_tariffs["new_group"])
    await charge_service.recalculate_for_student(db, student_id=student_id, period=PERIOD)

    after = await _charges(db, student_id)
    assert await charge_service.clear_manual_amount(db, charge_id=after[0]["id"])

    cleared = await _charges(db, student_id)
    assert cleared[0]["manual_minor"] is None, "«Вернуть к расчёту» обязано снимать сумму"
    assert _total(cleared) == NEW_PRICE, "после снятия действует новый тариф"

    # И повторный пересчёт не должен воскресить снятую сумму: иначе кнопка
    # работала бы «до следующего пересчёта», а это хуже, чем не работать.
    await charge_service.recalculate_for_student(db, student_id=student_id, period=PERIOD)
    again = await _charges(db, student_id)
    assert again[0]["manual_minor"] is None
    assert _total(again) == NEW_PRICE


async def test_closed_month_keeps_its_manual_amount(
    db: AsyncSession, two_tariffs: dict[str, int]
) -> None:
    """Закрытый месяц перевод не трогает: ни суммы, ни строки, ни группы."""
    student_id = two_tariffs["student_id"]
    await charge_service.recalculate_for_student(db, student_id=student_id, period=PERIOD)
    rows = await _charges(db, student_id)
    await charge_service.set_manual_amount(db, charge_id=rows[0]["id"], amount_minor=MANUAL)
    await charge_service.close_month(db, period=PERIOD, closed_by=None)

    await _subscribe(db, student_id, "self", two_tariffs["new_group"])
    await charge_service.recalculate_for_student(db, student_id=student_id, period=PERIOD)

    after = await _charges(db, student_id)
    closed = [r for r in after if r["status"] == "closed"]
    assert len(closed) == 1, "закрытая строка обязана остаться на месте"
    assert closed[0]["group_id"] == two_tariffs["old_group"]
    assert closed[0]["manual_minor"] == MANUAL


# ─────────── 2. Ручная цена группы не должна задваивать начисление ──────────


async def test_price_override_of_left_group_does_not_double_charge(
    db: AsyncSession, two_tariffs: dict[str, int]
) -> None:
    """Осиротевшая ручная цена прежней группы оживляет её строку.

    Строка покинутой группы удаляется только если «считать не из чего». Ручная
    цена группы — основание: `_base_price_minor` находит её по СТАРОЙ группе,
    строка выживает, и за один месяц выходит два начисления. Именно этот класс
    дал в tsk-630 «45 строк вместо 41».
    """
    student_id = two_tariffs["student_id"]
    await charge_service.set_price_override(
        db,
        student_id=student_id,
        group_id=two_tariffs["old_group"],
        price_minor=OLD_PRICE,
        note="tsk-634 проверка",
        created_by=None,
    )
    await charge_service.recalculate_for_student(db, student_id=student_id, period=PERIOD)
    assert len(await _charges(db, student_id)) == 1

    await _subscribe(db, student_id, "self", two_tariffs["new_group"])
    await charge_service.recalculate_for_student(db, student_id=student_id, period=PERIOD)

    after = await _charges(db, student_id)
    assert len(after) == 1, (
        "за один месяц вышло два начисления: ручная цена покинутой группы "
        f"оживила её строку — {after}"
    )
    assert _total(after) == NEW_PRICE, (
        "после перевода считаем по новому тарифу: ручная цена прежней группы "
        "к новой не относится"
    )


async def test_price_override_revives_when_student_returns(
    db: AsyncSession, two_tariffs: dict[str, int]
) -> None:
    """Запись ручной цены не удаляется: вернули на прежний тариф — цена ожила.

    Это цена вопроса за решение «не применять» вместо «удалять»: договорённость
    остаётся в базе, и возврат не требует заводить её заново руками.
    """
    student_id = two_tariffs["student_id"]
    await charge_service.set_price_override(
        db,
        student_id=student_id,
        group_id=two_tariffs["old_group"],
        price_minor=333_000,
        note="tsk-634 возврат",
        created_by=None,
    )
    await _subscribe(db, student_id, "self", two_tariffs["new_group"])
    await charge_service.recalculate_for_student(db, student_id=student_id, period=PERIOD)
    assert _total(await _charges(db, student_id)) == NEW_PRICE

    await _subscribe(db, student_id, "base", two_tariffs["old_group"])
    await charge_service.recalculate_for_student(db, student_id=student_id, period=PERIOD)

    back = await _charges(db, student_id)
    assert len(back) == 1
    assert _total(back) == 333_000, "ручная цена прежней группы обязана ожить"


# ───────────── 3. Предупреждение: ручные деньги видно ДО перевода ───────────


async def test_subscription_state_shows_manual_money(
    db: AsyncSession, two_tariffs: dict[str, int]
) -> None:
    """`GET /subscriptions/students/{id}` показывает ручные деньги ученика.

    Без этого экран смены тарифа не может предупредить: «сохраняем» защищает
    сумму месяца, но личную договорённость оператор всё равно должен видеть до
    того, как нажмёт.
    """
    student_id = two_tariffs["student_id"]
    await charge_service.recalculate_for_student(db, student_id=student_id, period=PERIOD)
    rows = await _charges(db, student_id)
    await charge_service.set_manual_amount(db, charge_id=rows[0]["id"], amount_minor=MANUAL)
    await charge_service.set_price_override(
        db,
        student_id=student_id,
        group_id=two_tariffs["old_group"],
        price_minor=OLD_PRICE,
        note="старая цена",
        created_by=None,
    )

    state = await subscription_service.student_state(db, student_id)
    manual = ManualPricingState(**state["manual_pricing"])

    assert [a.manual_minor for a in manual.monthly_amounts] == [MANUAL]
    assert manual.monthly_amounts[0].calculated_minor == OLD_PRICE, (
        "рядом с ручной суммой нужен расчёт — иначе не видно, о какой разнице речь"
    )
    assert [(p.price_minor, p.applies_now) for p in manual.group_prices] == [
        (OLD_PRICE, True)
    ]


async def test_manual_pricing_marks_left_group_price_as_inactive(
    db: AsyncSession, two_tariffs: dict[str, int]
) -> None:
    """Цена покинутой группы отдаётся с `applies_now = false`, а не молчит.

    Строка остаётся видимой намеренно: «висит в базе, но не действует» — это то,
    что оператору нужно знать, чтобы не искать, куда делась старая цена.
    """
    student_id = two_tariffs["student_id"]
    await charge_service.set_price_override(
        db,
        student_id=student_id,
        group_id=two_tariffs["old_group"],
        price_minor=OLD_PRICE,
        note=None,
        created_by=None,
    )
    await _subscribe(db, student_id, "self", two_tariffs["new_group"])

    state = await subscription_service.student_state(db, student_id)
    manual = ManualPricingState(**state["manual_pricing"])
    assert [(p.group_id, p.applies_now) for p in manual.group_prices] == [
        (two_tariffs["old_group"], False)
    ]


async def test_manual_pricing_is_empty_for_ordinary_student(
    db: AsyncSession, two_tariffs: dict[str, int]
) -> None:
    """У обычного ученика блок пустой — предупреждать не о чем."""
    state = await subscription_service.student_state(db, two_tariffs["student_id"])
    manual = ManualPricingState(**state["manual_pricing"])
    assert manual.is_empty
