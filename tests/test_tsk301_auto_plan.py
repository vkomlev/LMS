"""tsk-301: автоприсвоение тарифа (решение оператора 2026-08-08).

Два правила: регистрация даёт `demo`, появление занятий в расписании переводит на
`base`. Оба выведены из живого прогона Фазы 6, где выяснилось, что новый ученик
остаётся без тарифа, а при включённом гейте это сразу отказ.

**Главный тест здесь — не про то, что повышение работает, а про то, что оно НЕ
срабатывает с `base_legacy`.** На этом тарифе 37 действующих учеников со старой
ценой 2750/5500, а `base` — это 3000/6000. Правило без оговорки поднимало бы им
цену молча и задним числом: смена расписания и так зовёт пересчёт открытого месяца
(tsk-548), так что новая цена уехала бы в уже названную человеку сумму.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import subscription_service as subs
from app.services.auth import role_assign_service

pytestmark = pytest.mark.asyncio


async def _new_user(db: AsyncSession, *, with_role: bool) -> int:
    user_id = (
        await db.execute(
            text(
                "INSERT INTO users (full_name, email, is_active) "
                "VALUES ('tsk301 авто-тариф', :e, true) RETURNING id"
            ),
            {"e": f"tsk301-auto-{uuid.uuid4().hex[:12]}@example.test"},
        )
    ).scalar_one()
    if with_role:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = 'student' "
                "ON CONFLICT DO NOTHING"
            ),
            {"u": user_id},
        )
    return int(user_id)


async def _plan_of(db: AsyncSession, student_id: int) -> str | None:
    return (
        await db.execute(
            text(
                "SELECT p.code FROM student_subscription s "
                "  JOIN subscription_plan p ON p.id = s.plan_id "
                " WHERE s.student_id = :s AND s.ends_on IS NULL"
            ),
            {"s": student_id},
        )
    ).scalar()


async def _subscribe(db: AsyncSession, student_id: int, plan_code: str) -> None:
    await db.execute(
        text(
            "INSERT INTO student_subscription "
            "  (student_id, plan_id, pricing_group_id, starts_on) "
            "SELECT :s, id, pricing_group_id, CURRENT_DATE "
            "  FROM subscription_plan WHERE code = :c"
        ),
        {"s": student_id, "c": plan_code},
    )


@pytest_asyncio.fixture(scope="function")
async def slot_id(db: AsyncSession) -> int:
    """Активный слот расписания — минимум для правила «появилось занятие»."""
    teacher_id = await _new_user(db, with_role=False)
    value = (
        await db.execute(
            text(
                "INSERT INTO lesson_slot "
                "  (teacher_id, weekday, start_time, duration_minutes, is_active) "
                "VALUES (:t, 1, '10:00', 60, true) RETURNING id"
            ),
            {"t": teacher_id},
        )
    ).scalar_one()
    return int(value)


async def _put_in_schedule(db: AsyncSession, slot_id: int, student_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO lesson_slot_student (slot_id, student_id, is_active) "
            "VALUES (:s, :u, true)"
        ),
        {"s": slot_id, "u": student_id},
    )


# ───────────────────── Правило 1: регистрация → demo ────────────────────────


async def test_registration_gives_demo(db: AsyncSession) -> None:
    student_id = await _new_user(db, with_role=False)
    assigned = await role_assign_service.ensure_student_role(
        db, student_id, channel="magic_link", origin="auto_registration"
    )
    assert assigned is True
    assert await _plan_of(db, student_id) == "demo", (
        "новый ученик обязан получать тариф в том же событии, что и роль"
    )


async def test_default_plan_is_idempotent(db: AsyncSession) -> None:
    student_id = await _new_user(db, with_role=True)
    await _subscribe(db, student_id, "base")

    created = await subs.ensure_default_plan(db, student_id, channel="magic_link")
    assert created is False, "у кого тариф есть, второй не выдаётся"
    assert await _plan_of(db, student_id) == "base"


async def test_default_plan_failure_does_not_break_registration(
    db: AsyncSession, monkeypatch
) -> None:
    """Сбой выдачи тарифа не должен ломать вход.

    Тариф чинится присвоением, а несостоявшийся вход — нет.
    """

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("тариф не выдался")

    monkeypatch.setattr(subs, "ensure_default_plan", _boom)
    student_id = await _new_user(db, with_role=False)
    assigned = await role_assign_service.ensure_student_role(
        db, student_id, channel="tg_init", origin="auto_registration"
    )
    assert assigned is True, "роль обязана назначиться даже при сбое тарифа"


# ─────────────── Правило 2: появилось занятие → base (только с demo) ────────


async def test_schedule_upgrades_demo_to_base(
    db: AsyncSession, slot_id: int
) -> None:
    student_id = await _new_user(db, with_role=True)
    await _subscribe(db, student_id, "demo")
    await _put_in_schedule(db, slot_id, student_id)

    assert await subs.upgrade_on_schedule(db, student_id) is True
    assert await _plan_of(db, student_id) == "base"


async def test_schedule_assigns_base_when_no_plan(
    db: AsyncSession, slot_id: int
) -> None:
    student_id = await _new_user(db, with_role=True)
    await _put_in_schedule(db, slot_id, student_id)

    assert await subs.upgrade_on_schedule(db, student_id) is True
    assert await _plan_of(db, student_id) == "base"


@pytest.mark.parametrize(
    "plan_code", ["base_legacy", "test", "flagship", "adults", "alumni", "ai", "self"]
)
async def test_schedule_never_overwrites_other_plans(
    db: AsyncSession, slot_id: int, plan_code: str
) -> None:
    """Расписание не переписывает осознанно назначенный тариф.

    `base_legacy` здесь главный: перевод на `base` поднял бы цену с 2750 на 3000
    у 37 действующих учеников — молча, потому что смена расписания и так зовёт
    пересчёт открытого месяца.
    """
    student_id = await _new_user(db, with_role=True)
    await _subscribe(db, student_id, plan_code)
    await _put_in_schedule(db, slot_id, student_id)

    assert await subs.upgrade_on_schedule(db, student_id) is False
    assert await _plan_of(db, student_id) == plan_code


async def test_no_schedule_no_upgrade(db: AsyncSession) -> None:
    """Без занятия в расписании повышения нет — правило именно про занятия."""
    student_id = await _new_user(db, with_role=True)
    await _subscribe(db, student_id, "demo")

    assert await subs.upgrade_on_schedule(db, student_id) is False
    assert await _plan_of(db, student_id) == "demo"


async def test_upgrade_keeps_single_active_subscription(
    db: AsyncSession, slot_id: int
) -> None:
    """После повышения действующая подписка остаётся одна, прежняя закрыта.

    Инвариант держит частичный уникальный индекс; тест фиксирует, что повышение
    его не нарушает, а история сохраняется строкой.
    """
    student_id = await _new_user(db, with_role=True)
    await _subscribe(db, student_id, "demo")
    await _put_in_schedule(db, slot_id, student_id)
    await subs.upgrade_on_schedule(db, student_id)

    active = (
        await db.execute(
            text(
                "SELECT count(*) FROM student_subscription "
                " WHERE student_id = :s AND ends_on IS NULL"
            ),
            {"s": student_id},
        )
    ).scalar()
    total = (
        await db.execute(
            text("SELECT count(*) FROM student_subscription WHERE student_id = :s"),
            {"s": student_id},
        )
    ).scalar()
    assert (active, total) == (1, 2), "история повышения обязана сохраниться строкой"


async def test_upgrade_switches_pricing_group(
    db: AsyncSession, slot_id: int
) -> None:
    """Повышение меняет и тарифную группу — иначе деньги остались бы demo-шными."""
    student_id = await _new_user(db, with_role=True)
    await _subscribe(db, student_id, "demo")
    await _put_in_schedule(db, slot_id, student_id)
    await subs.upgrade_on_schedule(db, student_id)

    group_name = (
        await db.execute(
            text(
                "SELECT g.name FROM student_subscription s "
                "  JOIN pricing_group g ON g.id = s.pricing_group_id "
                " WHERE s.student_id = :s AND s.ends_on IS NULL"
            ),
            {"s": student_id},
        )
    ).scalar()
    assert group_name == "Базовый 2026"


# ──────────────────── Ручная смена тарифа (change_plan) ─────────────────────


async def test_change_plan_closes_previous_and_opens_new(db: AsyncSession) -> None:
    """Смена тарифа — это две строки, а не правка одной.

    Проверяем ровно то, ради чего запрещён прямой UPDATE: прошлая строка
    остаётся в истории с датой закрытия, действующей остаётся одна.
    """
    student_id = await _new_user(db, with_role=True)
    await _subscribe(db, student_id, "base")

    assert await subs.change_plan(
        db, student_id, "alumni", reason="закончил обучение"
    ) is True
    assert await _plan_of(db, student_id) == "alumni"

    rows = (
        await db.execute(
            text(
                "SELECT p.code, s.ends_on IS NULL AS is_current, s.pricing_group_id "
                "  FROM student_subscription s "
                "  JOIN subscription_plan p ON p.id = s.plan_id "
                " WHERE s.student_id = :s ORDER BY s.id"
            ),
            {"s": student_id},
        )
    ).all()
    assert [(r.code, r.is_current) for r in rows] == [
        ("base", False),
        ("alumni", True),
    ]
    assert rows[1].pricing_group_id is None, (
        "у «Выпускника» тарифной группы нет — значит и начислений не будет"
    )


async def test_change_plan_records_author(db: AsyncSession) -> None:
    """Кто перевёл — записывается: иначе смену тарифа не с кого спросить."""
    student_id = await _new_user(db, with_role=True)
    author_id = await _new_user(db, with_role=False)
    await _subscribe(db, student_id, "base")

    await subs.change_plan(
        db, student_id, "alumni", reason="закончил обучение", changed_by=author_id
    )
    assert (
        await db.execute(
            text(
                "SELECT changed_by FROM student_subscription "
                " WHERE student_id = :s AND ends_on IS NULL"
            ),
            {"s": student_id},
        )
    ).scalar() == author_id


async def test_change_plan_keeps_old_row_when_plan_unknown(db: AsyncSession) -> None:
    """Несуществующий тариф не должен оставить ученика без подписки вовсе.

    Закрытие и открытие идут одним savepoint именно поэтому: порознь ученик
    потерял бы права на несуществующем коде, и узнали бы мы об этом по жалобе.
    """
    student_id = await _new_user(db, with_role=True)
    await _subscribe(db, student_id, "base")

    assert await subs.change_plan(
        db, student_id, "no_such_plan", reason="опечатка в коде тарифа"
    ) is False
    assert await _plan_of(db, student_id) == "base", "прежняя подписка обязана уцелеть"
