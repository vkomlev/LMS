"""tsk-1124 Ф5: опрос «Пожелания к расписанию» — только ученикам детских групп.

Решение оператора 26.09: сетка опроса детская, у взрослых один фиксированный
слот — им опрос не показывается, напоминания не уходят, в сводке методиста и в
вёрстке расписания они не считаются.

Покрывает:
- ученик без групп (группа по умолчанию — детская) — в аудитории;
- ученик только взрослой группы — вне аудитории: `is_audience`, `is_pending`,
  `list_silent` (напоминания), `get_summary` (охват);
- ученик в обеих группах — в аудитории (он ходит и к детям);
- `effective_group_ids` и SQL-фрагмент дают одно и то же (один источник правила).
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import (
    schedule_group_service,
    schedule_preference_reminder_service,
    schedule_preference_service,
)


async def _student(db, *groups: str) -> int:
    u = Users(
        email=f"tsk1124s-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="tsk1124s-student", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, id FROM roles WHERE name = 'student' ON CONFLICT DO NOTHING"
        ),
        {"u": u.id},
    )
    for name in groups:
        await db.execute(
            text(
                "INSERT INTO user_schedule_group (user_id, group_id) "
                "SELECT :u, id FROM schedule_group WHERE name = :n"
            ),
            {"u": u.id, "n": name},
        )
    await db.commit()
    return u.id


KIDS = "Дети · Информатика"
ADULTS = "Взрослые · Тестирование"


@pytest.mark.asyncio
async def test_audience_by_group(db):
    plain = await _student(db)
    adult = await _student(db, ADULTS)
    both = await _student(db, KIDS, ADULTS)

    assert await schedule_preference_service.is_audience(db, plain) is True
    assert await schedule_preference_service.is_audience(db, both) is True
    assert await schedule_preference_service.is_audience(db, adult) is False
    assert await schedule_preference_service.is_pending(db, adult) is False
    assert await schedule_preference_service.is_pending(db, plain) is True


@pytest.mark.asyncio
async def test_reminders_and_summary_skip_adults(db):
    plain = await _student(db)
    adult = await _student(db, ADULTS)

    silent = {r["id"] for r in await schedule_preference_reminder_service.list_silent(db)}
    assert plain in silent and adult not in silent

    summary = await schedule_preference_service.get_summary(db)
    ids = {s["student_id"] for s in summary["students"]}
    assert plain in ids and adult not in ids


@pytest.mark.asyncio
async def test_effective_groups_single_source(db):
    """Python-функция исполняет тот же SQL-фрагмент, что и аудитория опроса."""
    kids = (await db.execute(text("SELECT id FROM schedule_group WHERE name = :n"), {"n": KIDS})).scalar_one()
    adults = (await db.execute(text("SELECT id FROM schedule_group WHERE name = :n"), {"n": ADULTS})).scalar_one()
    plain = await _student(db)
    adult = await _student(db, ADULTS)
    assert await schedule_group_service.effective_group_ids(db, plain) == [kids]
    assert await schedule_group_service.effective_group_ids(db, adult) == [adults]
    sql = schedule_group_service.effective_groups_sql(":uid")
    via_sql = [
        int(r[0])
        for r in (await db.execute(text(f"SELECT gid FROM {sql} AS e(gid) ORDER BY gid"), {"uid": adult})).all()
    ]
    assert via_sql == [adults]
