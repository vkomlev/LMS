"""tsk-1124 Ф1: схема групп расписания.

Покрывает:
- миграция завела ровно одну группу по умолчанию («Дети · Информатика») и
  взрослую группу с подсказкой тарифа плана `adults`;
- слот, вставленный без группы (ORM и сырой INSERT — оба живых пути
  создания), получает группу по умолчанию триггером;
- явная группа триггером не перетирается;
- вторую группу по умолчанию завести нельзя.
"""
from __future__ import annotations

import random
from datetime import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.lesson_slot import LessonSlot
from app.models.users import Users


async def _teacher(db) -> int:
    u = Users(
        email=f"tsk1124-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="tsk1124-teacher", tg_id=None,
    )
    db.add(u)
    await db.flush()
    return u.id


async def _group_id(db, name: str) -> int:
    return (await db.execute(text("SELECT id FROM schedule_group WHERE name = :n"), {"n": name})).scalar_one()


@pytest.mark.asyncio
async def test_seed_groups(db):
    """Одна группа по умолчанию — детская; у взрослой подсказка тарифа плана adults."""
    rows = (await db.execute(text(
        "SELECT name, audience, is_default FROM schedule_group WHERE is_default"
    ))).all()
    assert [(r.name, r.audience) for r in rows] == [("Дети · Информатика", "kids")]
    adults_pg = (await db.execute(text(
        "SELECT pricing_group_id FROM schedule_group WHERE name = 'Взрослые · Тестирование'"
    ))).scalar_one()
    plan_pg = (await db.execute(text(
        "SELECT pricing_group_id FROM subscription_plan WHERE code = 'adults'"
    ))).scalar()
    assert adults_pg == plan_pg


@pytest.mark.asyncio
async def test_orm_slot_without_group_gets_default(db):
    """ORM-вставка без group_id: триггер ставит группу по умолчанию, ORM её видит."""
    slot = LessonSlot(
        teacher_id=await _teacher(db), weekday=4, start_time=time(12, 0),
        duration_minutes=60, timezone="Europe/Moscow", is_active=True,
    )
    db.add(slot)
    await db.flush()
    await db.refresh(slot)
    assert slot.group_id == await _group_id(db, "Дети · Информатика")


@pytest.mark.asyncio
async def test_raw_insert_without_group_gets_default_and_explicit_kept(db):
    """Сырой INSERT без группы — детская; с явной группой — она и остаётся."""
    teacher_id = await _teacher(db)
    kids = await _group_id(db, "Дети · Информатика")
    adults = await _group_id(db, "Взрослые · Тестирование")
    plain = (await db.execute(text(
        "INSERT INTO lesson_slot (teacher_id, weekday, start_time, duration_minutes) "
        "VALUES (:t, 0, '10:00', 60) RETURNING group_id"
    ), {"t": teacher_id})).scalar_one()
    explicit = (await db.execute(text(
        "INSERT INTO lesson_slot (teacher_id, weekday, start_time, duration_minutes, group_id) "
        "VALUES (:t, 1, '10:00', 60, :g) RETURNING group_id"
    ), {"t": teacher_id, "g": adults})).scalar_one()
    assert (plain, explicit) == (kids, adults)


@pytest.mark.asyncio
async def test_second_default_group_rejected(db):
    """Частичный уникальный индекс не даёт завести вторую группу по умолчанию."""
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(text(
                "INSERT INTO schedule_group (audience, subject, name, is_default) "
                "VALUES ('adults', 'Python', 'tsk1124 второй default', true)"
            ))
