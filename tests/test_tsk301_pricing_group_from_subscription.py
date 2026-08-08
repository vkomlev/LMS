"""tsk-301: тарифная группа берётся из подписки, а не из курса (контракт §7).

Причина правки: «Базовый 3000», «Базовый 2750», «Self 1000» и «AI 1500» продают
ОДИН И ТОТ ЖЕ курс — курсом их не различить. `course_pricing` даёт одну группу на
курс, а резолвер внутри группы одноосный (частота ИЛИ сегмент), поэтому двумя
тарифами в одной группе это тоже не выражается.

Три исхода, и их нельзя схлопнуть в два:

* подписка с группой → **только** она (не «вдобавок к курсовой», иначе ученик
  Self, зачисленный на курс группы «Базовый», получит два начисления за один
  продукт);
* подписка без группы → начислений нет вовсе (Test, Demo, Выпускник);
* подписки нет → прежнее поведение, группы из проданных курсов.

Главный тест здесь — последний: **пока подписки никому не присвоены, деньги
считаются ровно как раньше.** Это и есть то свойство, которое делает выкат
безопасным: правка спит до Фазы 5.

Отдельно закреплено удаление устаревшей строки при смене тарифа. Производная
строка, у которой исчезло основание, не «остаётся как была» — она замирает со
старой суммой навсегда, потому что пересчёт по ней больше не проходит.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import charge_service, pricing_service

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(scope="function")
async def money_fixture(db: AsyncSession) -> dict[str, int]:
    """Ученик, зачисленный на платный курс группы «Базовый».

    Форма повторяет прод: ученик **без расписания** и с зачислением на курс —
    ровно тот случай, что у 18 человек из 52 (урок tsk-231: фикстура, не похожая
    на прод, зеленеет при неверном правиле).
    """
    student_id = (
        await db.execute(
            text(
                "INSERT INTO users (full_name, email, is_active) "
                "VALUES ('tsk301 ученик денег', :e, true) RETURNING id"
            ),
            {"e": f"tsk301-money-{uuid.uuid4().hex[:12]}@example.test"},
        )
    ).scalar_one()
    course_id = (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level) "
                "VALUES ('tsk301 платный курс', 'self_guided') RETURNING id"
            )
        )
    ).scalar_one()
    base_group = (
        await db.execute(
            text("SELECT id FROM pricing_group WHERE name = 'Базовый'")
        )
    ).scalar_one()
    self_group = (
        await db.execute(text("SELECT id FROM pricing_group WHERE name = 'Self'"))
    ).scalar_one()

    await db.execute(
        text(
            "INSERT INTO course_pricing (course_id, sale_status, group_id) "
            "VALUES (:c, 'paid', :g)"
        ),
        {"c": course_id, "g": base_group},
    )
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true)"
        ),
        {"u": student_id, "c": course_id},
    )
    return {
        "student_id": int(student_id),
        "course_id": int(course_id),
        "base_group": int(base_group),
        "self_group": int(self_group),
    }


async def _subscribe(
    db: AsyncSession, student_id: int, plan_code: str, group_id: int | None
) -> None:
    await db.execute(
        text(
            "INSERT INTO student_subscription "
            "  (student_id, plan_id, pricing_group_id, starts_on) "
            "SELECT :s, id, :g, CURRENT_DATE FROM subscription_plan WHERE code = :c"
        ),
        {"s": student_id, "g": group_id, "c": plan_code},
    )


# ───────────────── Главное свойство: без подписок ничего не меняется ────────


async def test_without_subscription_groups_come_from_courses(
    db: AsyncSession, money_fixture: dict[str, int]
) -> None:
    """Пока подписки не присвоены — прежнее поведение, группа из курса.

    Это то самое свойство, ради которого правку можно катить до Фазы 5: она
    спит, пока подписок нет.
    """
    groups = await pricing_service.billing_group_ids(
        db, student_id=money_fixture["student_id"]
    )
    assert groups == [money_fixture["base_group"]]


async def test_subscription_overrides_course_group(
    db: AsyncSession, money_fixture: dict[str, int]
) -> None:
    """Подписка Self перекрывает группу курса, а не добавляется к ней."""
    await _subscribe(
        db, money_fixture["student_id"], "self", money_fixture["self_group"]
    )
    groups = await pricing_service.billing_group_ids(
        db, student_id=money_fixture["student_id"]
    )
    assert groups == [money_fixture["self_group"]], (
        "группа курса должна быть вытеснена, иначе выйдет два начисления за один продукт"
    )


async def test_subscription_without_group_means_no_money(
    db: AsyncSession, money_fixture: dict[str, int]
) -> None:
    """Test, Demo и Выпускник начислений не порождают вовсе."""
    await _subscribe(db, money_fixture["student_id"], "alumni", None)
    groups = await pricing_service.billing_group_ids(
        db, student_id=money_fixture["student_id"]
    )
    assert groups == []


# ─────────────────────── Расчёт цены по подписке ────────────────────────────


async def test_self_subscriber_without_schedule_gets_full_price(
    db: AsyncSession, money_fixture: dict[str, int]
) -> None:
    """Ученик Self без расписания получает полную цену тарифа.

    Так уже работает пропорция: при нуле ожидаемых занятий она не применяется.
    Тариф без занятий вписывается в существующий расчёт без исключений — это и
    есть содержание принципа «расписание порождает деньги».
    """
    await _subscribe(
        db, money_fixture["student_id"], "self", money_fixture["self_group"]
    )
    rows = await pricing_service.list_student_pricing(db)
    mine = [r for r in rows if r.student_id == money_fixture["student_id"]]
    assert len(mine) == 1, "ученик обязан попасть в расчёт ровно один раз"

    groups = mine[0].groups
    assert len(groups) == 1, "групп у ученика с подпиской ровно одна"
    assert groups[0].group_id == money_fixture["self_group"]
    assert groups[0].price_minor == 100_000, "Self — 1000 ₽ полной ценой"


async def test_subscriber_appears_without_any_paid_course(
    db: AsyncSession
) -> None:
    """Подписчик без зачислений вообще тоже попадает в расчёт.

    Self и AI курсов не имеют; если бы расчёт по-прежнему начинался с зачислений,
    такой ученик не получил бы начисления никогда — он платит, а счёта нет.
    """
    student_id = (
        await db.execute(
            text(
                "INSERT INTO users (full_name, email, is_active) "
                "VALUES ('tsk301 подписчик без курсов', :e, true) RETURNING id"
            ),
            {"e": f"tsk301-bare-{uuid.uuid4().hex[:12]}@example.test"},
        )
    ).scalar_one()
    ai_group = (
        await db.execute(text("SELECT id FROM pricing_group WHERE name = 'AI'"))
    ).scalar_one()
    await _subscribe(db, int(student_id), "ai", int(ai_group))

    rows = await pricing_service.list_student_pricing(db)
    mine = [r for r in rows if r.student_id == student_id]
    assert len(mine) == 1
    assert mine[0].groups[0].price_minor == 150_000, "AI — 1500 ₽"


# ──────────────── Смена тарифа не оставляет мёртвых начислений ──────────────


async def test_switching_tariff_removes_stale_charge(
    db: AsyncSession, money_fixture: dict[str, int]
) -> None:
    """Начисление по прежней группе удаляется, а не замирает со старой суммой.

    Пересчёт идёт по НОВОМУ набору групп; если не добрать группы с уже открытыми
    строками, старая строка не попадёт в цикл ни разу и останется навсегда —
    производная величина без основания.
    """
    student_id = money_fixture["student_id"]
    period = date.today().replace(day=1)

    await db.execute(
        text(
            "INSERT INTO student_monthly_charge "
            "  (student_id, group_id, period, calculated_minor, status) "
            "VALUES (:s, :g, :p, 550000, 'open')"
        ),
        {"s": student_id, "g": money_fixture["base_group"], "p": period},
    )
    await _subscribe(db, student_id, "self", money_fixture["self_group"])

    await charge_service.recalculate_for_student(db, student_id=student_id, period=period)

    rows = (
        await db.execute(
            text(
                "SELECT group_id, calculated_minor FROM student_monthly_charge "
                " WHERE student_id = :s AND period = :p"
            ),
            {"s": student_id, "p": period},
        )
    ).all()
    groups = {int(r.group_id): int(r.calculated_minor) for r in rows}
    assert money_fixture["base_group"] not in groups, (
        "начисление по прежней группе осталось — оно замрёт со старой суммой"
    )
    assert groups.get(money_fixture["self_group"]) == 100_000
