"""tsk-1088: тариф взрослых — статичная 8000 ₽/мес, старая сетка за `adults_legacy`.

Проверяется на НАСТОЯЩЕЙ БД после миграции `tsk1088_adults_static_price` и через
штатный путь назначения (`subscription_service.change_plan`) — он копирует
группу плана в подписку, то есть проверяет и связку «план → группа».

- `adults` → 8000 ₽ и при 1, и при 2 занятиях в неделю;
- `adults_legacy` → 3500 / 7000 по частоте, как было;
- статичная цена не рождает «расхождение цены и расписания» (tsk-557/1024);
- курсы взрослых продаются по новой группе (публичная цена лендингов, tsk-1070).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import pricing_service, subscription_service

pytestmark = pytest.mark.asyncio


async def _user(db: AsyncSession, name: str) -> int:
    """Пользователь с уникальной почтой."""
    return int(
        (
            await db.execute(
                text(
                    "INSERT INTO users (full_name, email, is_active) "
                    "VALUES (:n, :e, true) RETURNING id"
                ),
                {"n": name, "e": f"tsk1088-{uuid.uuid4().hex[:12]}@example.test"},
            )
        ).scalar_one()
    )


async def _student_with_slots(db: AsyncSession, plan_code: str, weekly: int) -> int:
    """Ученик на плане `plan_code` с `weekly` активными слотами в неделю."""
    student_id = await _user(db, f"tsk1088 ученик {plan_code} x{weekly}")
    teacher_id = await _user(db, "tsk1088 преподаватель")
    for weekday in range(weekly):
        slot_id = (
            await db.execute(
                text(
                    "INSERT INTO lesson_slot (teacher_id, weekday, start_time, duration_minutes) "
                    "VALUES (:t, :wd, '10:00', 60) RETURNING id"
                ),
                {"t": teacher_id, "wd": weekday},
            )
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO lesson_slot_student (slot_id, student_id, is_active) "
                "VALUES (:s, :u, true)"
            ),
            {"s": slot_id, "u": student_id},
        )
    assert await subscription_service.change_plan(
        db, student_id, plan_code, reason="tsk-1088 тест"
    ), f"план {plan_code} не назначился"
    return student_id


async def _price(db: AsyncSession, student_id: int) -> tuple[str, int | None]:
    """Имя группы и расчётная цена единственной группы ученика."""
    rows = [r for r in await pricing_service.list_student_pricing(db) if r.student_id == student_id]
    assert len(rows) == 1, "ученик обязан попасть в расчёт ровно один раз"
    groups = rows[0].groups
    assert len(groups) == 1
    return groups[0].group_name, groups[0].price_minor


@pytest.mark.parametrize("weekly", [1, 2])
async def test_adults_is_flat_8000(db: AsyncSession, weekly: int) -> None:
    student_id = await _student_with_slots(db, "adults", weekly)
    group_name, price = await _price(db, student_id)
    assert group_name == "Обучение взрослых 2026"
    assert price == 800_000, f"при {weekly} занятиях в неделю цена обязана быть 8000 ₽"


@pytest.mark.parametrize(("weekly", "expected"), [(1, 350_000), (2, 700_000)])
async def test_adults_legacy_keeps_frequency_grid(
    db: AsyncSession, weekly: int, expected: int
) -> None:
    student_id = await _student_with_slots(db, "adults_legacy", weekly)
    group_name, price = await _price(db, student_id)
    assert group_name == "Обучение взрослых"
    assert price == expected


async def test_flat_price_has_no_schedule_discrepancy(db: AsyncSession) -> None:
    """Ручная цена 8000 на статичной группе не выводит частоту и не «расходится».

    Обратный вывод частоты идёт только по ступеням `attendance_frequency`, а у
    статичной группы их нет, — поэтому флаг расхождения с расписанием не
    загорается ни при 1, ни при 2 слотах.
    """
    student_id = await _student_with_slots(db, "adults", 1)
    group_id = (
        await db.execute(
            text("SELECT id FROM pricing_group WHERE name = 'Обучение взрослых 2026'")
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO student_price_override (student_id, group_id, price_minor) "
            "VALUES (:s, :g, 800000)"
        ),
        {"s": student_id, "g": group_id},
    )
    resolution = await pricing_service.resolve_attendance_frequency(db, student_id=student_id)
    assert resolution.source == "schedule"
    assert resolution.price_weekly_lessons is None
    assert resolution.discrepancy is False


async def test_adults_legacy_mirrors_adults_rights(db: AsyncSession) -> None:
    """Legacy-план отличается от `adults` только ценой, не правами."""
    rows = (
        await db.execute(
            text(
                "SELECT code, ai_tutor_limit, code_review, teacher_escalation, lessons, "
                "       content, is_active "
                "  FROM subscription_plan WHERE code IN ('adults', 'adults_legacy')"
            )
        )
    ).all()
    by_code = {r.code: tuple(r)[1:] for r in rows}
    assert set(by_code) == {"adults", "adults_legacy"}
    assert by_code["adults"] == by_code["adults_legacy"]


async def test_adult_courses_sold_by_new_group(db: AsyncSession) -> None:
    """На старой группе не осталось продаваемых курсов — лендинги видят 8000."""
    left = (
        await db.execute(
            text(
                "SELECT count(*) FROM course_pricing cp "
                "  JOIN pricing_group g ON g.id = cp.group_id "
                " WHERE g.name = 'Обучение взрослых'"
            )
        )
    ).scalar_one()
    assert left == 0
