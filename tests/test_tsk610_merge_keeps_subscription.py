"""tsk-610: слияние учёток не должно терять тариф и перерыв.

Прод-случай: ученик 4525 («Владимир Грабовский», тариф `base_legacy` с тарифной
группой 1) зарегистрировался заново — 4560. Автослияние при регистрации
(tsk-455) перенесло расписание, курсы, занятия и почту, но подписка и перерыв в
списках переноса не значились: обе таблицы (tsk-301, tsk-511) появились позже
самих списков. В итоге на живой учётке остался `demo` без тарифной группы,
`billing_group_ids` вернул пусто, и человек две недели ходил на занятия,
невидимый для начислений. `verify_merge` при этом молчал: он проверял ровно те
же таблицы, в которые писал.

Сам перенос подписки, квоты и пакетов проверяет соседний файл
`test_tsk301_merge_subscription.py` (правило «автоматический `demo` уступает,
осознанный тариф цели остаётся»). Здесь — то, что к нему примыкает:

- **старая цена не превращается в новую**: `base_legacy` переживает слияние, и
  автоприсвоение по расписанию его не трогает (иначе 5 500 ₽ молча стали бы
  6 000 ₽ — ровно та беда, ради которой в tsk-301 написан `UPGRADABLE_FROM`);
- перерыв уезжает на живую учётку: без него месяц считается полным, хотя
  человек не занимался;
- кеш доступности подкурсов у слитой учётки удаляется, а не тащится;
- расписание, приехавшее слиянием, включает автоприсвоение тарифа — раньше его
  звал только календарь, и «ученик с занятиями на demo» не чинился сам.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.user_merge_service import merge_users

pytestmark = pytest.mark.asyncio


async def _new_user(db: AsyncSession, name: str) -> int:
    return int(
        (
            await db.execute(
                text(
                    "INSERT INTO users (full_name, email, is_active) "
                    "VALUES (:n, :e, true) RETURNING id"
                ),
                {"n": name, "e": f"tsk610-{uuid.uuid4().hex[:12]}@example.test"},
            )
        ).scalar_one()
    )


async def _subscribe(
    db: AsyncSession, student_id: int, plan_code: str, *, starts_on: date
) -> int:
    return int(
        (
            await db.execute(
                text(
                    "INSERT INTO student_subscription "
                    "  (student_id, plan_id, pricing_group_id, starts_on) "
                    "SELECT :s, id, pricing_group_id, :d "
                    "  FROM subscription_plan WHERE code = :c "
                    "RETURNING id"
                ),
                {"s": student_id, "c": plan_code, "d": starts_on},
            )
        ).scalar_one()
    )


async def _slot(db: AsyncSession, student_id: int, teacher_id: int) -> None:
    slot_id = (
        await db.execute(
            text(
                "INSERT INTO lesson_slot "
                "(teacher_id, weekday, start_time, duration_minutes, timezone, is_active) "
                "VALUES (:t, 0, '11:00', 60, 'Europe/Moscow', true) RETURNING id"
            ),
            {"t": teacher_id},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO lesson_slot_student (slot_id, student_id, is_active) "
            "VALUES (:s, :u, true)"
        ),
        {"s": slot_id, "u": student_id},
    )


async def _active_plan(db: AsyncSession, student_id: int) -> tuple[str | None, int | None]:
    row = (
        await db.execute(
            text(
                "SELECT p.code, s.pricing_group_id FROM student_subscription s "
                "  JOIN subscription_plan p ON p.id = s.plan_id "
                " WHERE s.student_id = :s AND s.ends_on IS NULL"
            ),
            {"s": student_id},
        )
    ).first()
    return (row.code, row.pricing_group_id) if row else (None, None)


async def test_billable_subscription_moves_and_demo_closes(db: AsyncSession):
    """Тариф с группой переезжает на живую учётку, `demo` закрывается.

    Дословный прод-сценарий 4525 → 4560.
    """
    teacher_id = await _new_user(db, "tsk610 преподаватель")
    source_id = await _new_user(db, "Владимир Грабовский")
    target_id = await _new_user(db, "Грабовский Владимир Антонович")
    await _subscribe(db, source_id, "base_legacy", starts_on=date(2026, 8, 8))
    demo_id = await _subscribe(db, target_id, "demo", starts_on=date(2026, 8, 10))
    await _slot(db, target_id, teacher_id)
    await db.commit()

    assert await merge_users(db, source_id=source_id, target_id=target_id) is True
    await db.commit()

    code, group_id = await _active_plan(db, target_id)
    assert code == "base_legacy", "тариф с тарифной группой обязан пережить слияние"
    assert group_id is not None, "без группы ученик снова невидим для денег"

    closed = (
        await db.execute(
            text("SELECT ends_on FROM student_subscription WHERE id = :i"),
            {"i": demo_id},
        )
    ).scalar()
    assert closed is not None, "demo обязан закрыться: действующая подписка одна"

    left = (
        await db.execute(
            text("SELECT count(*) FROM student_subscription WHERE student_id = :s"),
            {"s": source_id},
        )
    ).scalar_one()
    assert left == 0, "на слитой учётке подписок остаться не должно"


async def test_schedule_upgrade_fires_when_schedule_arrives_by_merge(db: AsyncSession):
    """Расписание приехало слиянием → автоприсвоение тарифа сработало.

    Раньше `upgrade_on_schedule` звал только календарь, поэтому ученик, чьё
    расписание появилось при слиянии, оставался на `demo` навсегда.
    """
    teacher_id = await _new_user(db, "tsk610 преподаватель-2")
    source_id = await _new_user(db, "Пустая учётка")
    target_id = await _new_user(db, "Живая учётка")
    await _subscribe(db, target_id, "demo", starts_on=date(2026, 8, 10))
    await _slot(db, source_id, teacher_id)
    await db.commit()

    assert await merge_users(db, source_id=source_id, target_id=target_id) is True
    await db.commit()

    code, group_id = await _active_plan(db, target_id)
    assert code == "base", "расписание переехало — тариф обязан подняться с demo"
    assert group_id is not None


async def test_break_moves_to_live_account(db: AsyncSession):
    """Перерыв — основание неполного месяца, он обязан уехать вместе с человеком."""
    source_id = await _new_user(db, "Отъезд источник")
    target_id = await _new_user(db, "Отъезд цель")
    await db.execute(
        text(
            "INSERT INTO student_break (student_id, starts_on, ends_on, note) "
            "VALUES (:s, DATE '2026-07-22', DATE '2026-08-04', 'Отъезд')"
        ),
        {"s": source_id},
    )
    await db.commit()

    assert await merge_users(db, source_id=source_id, target_id=target_id) is True
    await db.commit()

    moved = (
        await db.execute(
            text("SELECT count(*) FROM student_break WHERE student_id = :s"),
            {"s": target_id},
        )
    ).scalar_one()
    assert moved == 1
    left = (
        await db.execute(
            text("SELECT count(*) FROM student_break WHERE student_id = :s"),
            {"s": source_id},
        )
    ).scalar_one()
    assert left == 0


async def test_course_state_cache_is_dropped_not_carried(db: AsyncSession):
    """Кеш доступности подкурсов у слитой учётки удаляется, а не тащится.

    Он производный: у живой учётки свой, и он пересчитывается. Строки source
    иначе остались бы мусором, который никто не обновляет.
    """
    source_id = await _new_user(db, "Кеш источник")
    target_id = await _new_user(db, "Кеш цель")
    course_id = (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level) "
                "VALUES ('tsk610 курс', 'self_guided') RETURNING id"
            )
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO student_course_state (student_id, course_id, state) "
            "VALUES (:s, :c, 'NOT_STARTED')"
        ),
        {"s": source_id, "c": course_id},
    )
    await db.commit()

    assert await merge_users(db, source_id=source_id, target_id=target_id) is True
    await db.commit()

    left = (
        await db.execute(
            text("SELECT count(*) FROM student_course_state WHERE student_id = :s"),
            {"s": source_id},
        )
    ).scalar_one()
    assert left == 0
