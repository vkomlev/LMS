"""tsk-996, группа 5 — деньги, кураторы, лиды: порядок не решает план запроса.

Каждый тест создаёт ничью по ключу сортировки (тёзки, одна дата, одно число),
кладёт строку с меньшим id в конец кучи и прогоняет вызов под всеми планами из
`tests/plan_order.py`. Ожидание — детерминированный порядок с добивкой по id.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services import (
    charge_cron_service,
    charge_service,
    curator_service,
    lead_service,
    payment_reminder_service,
    quiz_funnel_bot_service,
    quiz_funnel_service,
)
from tests.plan_order import assert_same, move_to_heap_end, run_under_plans
from tests.test_tsk505_marketer_pricing import _new_course, _new_group, _new_user
from tests.test_tsk511_charges_breaks import _slot_on

pytestmark = pytest.mark.asyncio

#: Прошедший месяц: страж сдвига смотрит только на прошлое.
PAST = date(2025, 1, 1)
#: «Сегодня» далеко впереди: любой месяц уже просрочен.
FAR_TODAY = date(2100, 1, 1)


async def _namesakes(db, n: int, name: str, *, role: str = "student") -> list[int]:
    """`n` пользователей с одинаковым именем; id по возрастанию."""
    ids = [(await _new_user(db, role=role, name=f"g5-{i}"))[0] for i in range(n)]
    await db.execute(
        text("UPDATE users SET full_name = :n WHERE id = ANY(:ids)"), {"n": name, "ids": ids}
    )
    await db.commit()
    return sorted(ids)


async def _charge(db, *, student_id: int, group_id: int, period: date, frozen: int | None = None) -> int:
    """Строка начисления месяца (100 руб.), при `frozen` — со снимком итога."""
    charge_id = (
        await db.execute(
            text(
                "INSERT INTO student_monthly_charge "
                "(student_id, group_id, period, calculated_minor, frozen_total_minor) "
                "VALUES (:s, :g, :p, 10000, :f) RETURNING id"
            ),
            {"s": student_id, "g": group_id, "p": period, "f": frozen},
        )
    ).scalar()
    await db.commit()
    return int(charge_id)


async def test_unbilled_namesakes_order_does_not_depend_on_plan(db):
    """Детектор «ходит, но не выставлен»: тёзки — по id (уходит в примеры уведомления)."""
    teacher_id, _ = await _new_user(db, role="teacher", name="g5-t")
    ids = await _namesakes(db, 3, "zz996 Неначисленный")
    for sid in ids:
        await _slot_on(db, student_id=sid, teacher_id=teacher_id, weekday=0)
    await move_to_heap_end(db, "users", ids[0])

    async def call():
        rows = await charge_cron_service.find_unbilled_active_students(db, period=PAST)
        return [r["student_id"] for r in rows if r["student_id"] in ids]

    assert_same(await run_under_plans(db, call), ids)


async def test_shifted_past_months_order_does_not_depend_on_plan(db):
    """Страж сдвига прошлого: тёзки и две группы ученика — по группе, затем по id строки."""
    ids = await _namesakes(db, 2, "zz996 Сдвинутый")
    g_a = await _new_group(db, "g5-a", [("Общий", 10000, None, None)])
    g_b = await _new_group(db, "g5-b", [("Общий", 10000, None, None)])
    await db.execute(
        text("UPDATE pricing_group SET name = CASE id WHEN :a THEN :na ELSE :nb END "
             "WHERE id IN (:a, :b)"),
        {"a": g_a, "b": g_b, "na": "zz996-g5-A", "nb": "zz996-g5-B"},
    )
    await db.commit()
    # Ученик 1 в группе B, ученик 0 в группах B и A: всё в одном месяце, у всех
    # одно имя — порядок держат только pg.name и ch.id.
    c1 = await _charge(db, student_id=ids[1], group_id=g_b, period=PAST, frozen=1)
    c0b = await _charge(db, student_id=ids[0], group_id=g_b, period=PAST, frozen=1)
    c0a = await _charge(db, student_id=ids[0], group_id=g_a, period=PAST, frozen=1)
    await move_to_heap_end(db, "student_monthly_charge", c1)
    mine = {c1, c0b, c0a}

    async def call():
        rows = await charge_service.find_shifted_past_months(db, today=FAR_TODAY)
        return [r["charge_id"] for r in rows if r["charge_id"] in mine]

    assert_same(await run_under_plans(db, call), [c0a, c1, c0b])


async def test_reminder_debtors_order_does_not_depend_on_plan(db):
    """Список должников: тёзки в одном месяце — по ученику, затем по группе."""
    ids = await _namesakes(db, 3, "zz996 Должник")
    group_id = await _new_group(db, "g5-debt", [("Общий", 10000, None, None)])
    charges = [await _charge(db, student_id=sid, group_id=group_id, period=PAST) for sid in ids]
    await move_to_heap_end(db, "student_monthly_charge", charges[0])
    await move_to_heap_end(db, "users", ids[0])

    async def call():
        rows = await payment_reminder_service.list_overdue(db, today=FAR_TODAY)
        return [r.student_id for r in rows if r.student_id in ids]

    assert_same(await run_under_plans(db, call), ids)


async def test_reminder_last_subscription_choice_does_not_depend_on_plan(db):
    """Учится или выпускник — по последней подписке, при любом плане.

    Две открытые подписки сейчас запрещает индекс `uq_student_subscription_active`,
    поэтому ничьи в LATERAL нет; тест держит инвариант (индекс на месте) и
    результат: смена тарифа тем же днём (старая закрыта той же датой) даёт
    «учится» по новой подписке.
    """
    idx = (
        await db.execute(
            text(
                "SELECT i.indisunique, pg_get_expr(i.indpred, i.indrelid) AS pred "
                "  FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                " WHERE c.relname = 'uq_student_subscription_active'"
            )
        )
    ).one()
    assert idx.indisunique and "ends_on IS NULL" in idx.pred

    (sid,) = await _namesakes(db, 1, "zz996 Переведённый")
    group_id = await _new_group(db, "g5-sub", [("Общий", 10000, None, None)])
    await _charge(db, student_id=sid, group_id=group_id, period=PAST)
    plans = {}
    for code, works in (("zz996-alumni", False), ("zz996-active", True)):
        plans[code] = (
            await db.execute(
                text(
                    "INSERT INTO subscription_plan (code, name, course_work) "
                    "VALUES (:c, :c, :w) RETURNING id"
                ),
                {"c": code, "w": works},
            )
        ).scalar()
    day = date(2025, 2, 1)
    old_id = (
        await db.execute(
            text(
                "INSERT INTO student_subscription (student_id, plan_id, starts_on, ends_on) "
                "VALUES (:s, :p, :d, :d) RETURNING id"
            ),
            {"s": sid, "p": plans["zz996-alumni"], "d": day},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO student_subscription (student_id, plan_id, starts_on) "
            "VALUES (:s, :p, :d)"
        ),
        {"s": sid, "p": plans["zz996-active"], "d": day},
    )
    await db.commit()
    await move_to_heap_end(db, "student_subscription", int(old_id))

    async def call():
        active = await payment_reminder_service.list_overdue(db, today=FAR_TODAY)
        alumni = await payment_reminder_service.list_alumni_debts(db, today=FAR_TODAY)
        return (
            [r.student_id for r in active if r.student_id == sid],
            [r.student_id for r in alumni if r.student_id == sid],
        )

    assert_same(await run_under_plans(db, call), ([sid], []))


async def test_bot_due_batch_does_not_depend_on_plan(db, monkeypatch):
    """Пачка напоминаний бота: при равном сроке и LIMIT — младшие id."""
    monkeypatch.setattr(quiz_funnel_service._settings, "quiz_funnel_enabled", True)
    monkeypatch.setattr(quiz_funnel_service._settings, "quiz_funnel_reminders_enabled", True)
    course_id = await _new_course(db, "g5-quiz")
    due = datetime(2000, 1, 1, tzinfo=timezone.utc)
    ids: list[int] = []
    # Гостевые сессии по убыванию uuid: соединение с прохождением, идущее по
    # uuid, ставит младший id последним — как и куча после перекладки.
    base = uuid.uuid4().hex[:20]
    for i in range(3):
        gs = (
            await db.execute(
                text("INSERT INTO guest_session (id) VALUES (CAST(:g AS uuid)) RETURNING id"),
                {"g": f"{base}{9 - i:012x}"},
            )
        ).scalar()
        ids.append(
            (
                await db.execute(
                    text(
                        "INSERT INTO quiz_funnel_bot_lead "
                        "(start_token, guest_session_id, quiz_course_id, tg_id, next_reminder_at) "
                        "VALUES (:t, :g, :c, :tg, :due) RETURNING id"
                    ),
                    {"t": f"zz996-g5-{i}-{gs}", "g": gs, "c": course_id, "tg": 996000 + i, "due": due},
                )
            ).scalar()
        )
    await db.commit()
    ids.sort()
    # Перекладка `id = id` идёт HOT-обновлением и порядок в индексе по сроку не
    # меняет; правка самого срока туда-обратно ставит младший id последним и там.
    for moment in (due + timedelta(seconds=1), due):
        await db.execute(
            text("UPDATE quiz_funnel_bot_lead SET next_reminder_at = :t WHERE id = :id"),
            {"t": moment, "id": ids[0]},
        )
    # Срок раньше любого чужого: пачка из двух целиком наша.
    await db.execute(
        text("UPDATE quiz_funnel_bot_lead SET next_reminder_at = :due + interval '1 day' "
             "WHERE next_reminder_at <= :due AND id <> ALL(:ids)"),
        {"due": due, "ids": ids},
    )

    async def call():
        rows = await quiz_funnel_bot_service.list_due(db, limit=2)
        return [r.bot_lead_id for r in rows]

    assert_same(await run_under_plans(db, call), ids[:2])


async def test_student_search_limit_does_not_depend_on_plan(db):
    """Поиск ученика для лида: тёзки на границе LIMIT — младшие id."""
    ids = await _namesakes(db, 3, "Zzgfivetest Тёзкин")
    await move_to_heap_end(db, "users", ids[0])

    async def call():
        rows = await lead_service.search_students(db, q="zzgfivetest", limit=2)
        return [r.id for r in rows]

    assert_same(await run_under_plans(db, call), ids[:2])


async def test_curator_coverage_ties_do_not_depend_on_plan(db):
    """Покрытие кураторов: равное число учеников — по id куратора.

    Добивка `u.id` выбрана равной порядку, который уже дают типичные планы
    (группировка по u.id), поэтому тест фиксирует инвариант, а не ловит сбой.
    """
    curators = sorted([(await _new_user(db, role="teacher", name=f"g5-c{i}"))[0] for i in range(10)])
    for cid in curators:
        sid, _ = await _new_user(db, role="student", name=f"g5-cs{cid}")
        await db.execute(
            text("INSERT INTO student_curator (student_id, curator_id, source) "
                 "VALUES (:s, :c, 'manual')"),
            {"s": sid, "c": cid},
        )
    await db.commit()
    await move_to_heap_end(db, "users", curators[0])

    async def call():
        res = await curator_service.coverage(db)
        return [c["curator_id"] for c in res["curators"] if c["curator_id"] in curators]

    assert_same(await run_under_plans(db, call), curators)
