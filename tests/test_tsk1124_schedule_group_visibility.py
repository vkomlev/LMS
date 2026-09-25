"""tsk-1124 Ф3: ученик видит и выбирает только слоты своих групп.

Сцена: один преподаватель ведёт детский слот (пн 12:00, в сетке) и взрослый
(пт 12:00 — вне детской сетки, как у взрослых тестировщиков). Ученик без групп
— детский (группа по умолчанию), взрослый — в группе «Взрослые · Тестирование».

Покрывает каждый путь ученика из инвентаря спека:
- выдача записи `get_bookable` и запись `join_slot` (чужой слот — 404);
- свободные окна площадок `get_free_slots` (по умолчанию детские, явно — группа);
- варианты переноса `list_available_slots` и приём `reschedule_occurrence`;
- разовая запись учеником `create_ad_hoc_occurrence` (чужой слот — 422),
  преподавателю правило не мешает;
- присоединение к занятию и список занятий для присоединения;
- выбор преподавателя по времени.
Плюс сторож: условие группы не копируется по сервисам.
"""
from __future__ import annotations

import random
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from app.models.users import Users
from app.schemas.schedule_preference import SchedulePreferenceWrite
from app.services import (
    lesson_calendar_service,
    lesson_occurrence_service,
    schedule_booking_service,
    schedule_preference_service,
)
from app.utils.exceptions import DomainError

MSK = ZoneInfo("Europe/Moscow")
MONDAY, FRIDAY = 0, 4


async def _user(db, role: str) -> int:
    u = Users(
        email=f"tsk1124v-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name=f"tsk1124v-{role}", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, id FROM roles WHERE name = :n ON CONFLICT DO NOTHING"
        ),
        {"u": u.id, "n": role},
    )
    await db.commit()
    return u.id


async def _gid(db, name: str) -> int:
    return (await db.execute(text("SELECT id FROM schedule_group WHERE name = :n"), {"n": name})).scalar_one()


async def _slot(db, teacher_id: int, weekday: int, group_id: int | None = None) -> int:
    sid = (await db.execute(
        text(
            "INSERT INTO lesson_slot (teacher_id, weekday, start_time, duration_minutes, group_id) "
            "VALUES (:t, :w, '12:00', 60, COALESCE(:g, (SELECT id FROM schedule_group WHERE is_default))) "
            "RETURNING id"
        ),
        {"t": teacher_id, "w": weekday, "g": group_id},
    )).scalar_one()
    await db.execute(
        text("INSERT INTO lesson_slot_teacher (slot_id, teacher_id, is_active) VALUES (:s, :t, true)"),
        {"s": sid, "t": teacher_id},
    )
    await db.commit()
    return sid


def _next_at(weekday: int) -> datetime:
    d = date.today() + timedelta(days=1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return datetime.combine(d, time(12, 0), tzinfo=MSK).astimezone(timezone.utc)


async def _fill_pref(db, student_id: int) -> None:
    await schedule_preference_service.save_preference(
        db, student_id,
        SchedulePreferenceWrite(
            lessons_per_week=1,
            hours=[{"weekday": MONDAY, "start_time": "12:00", "kind": "preferred"}],
        ),
        changed_by=student_id,
    )


@pytest.fixture
async def scene(db):
    teacher = await _user(db, "teacher")
    adults = await _gid(db, "Взрослые · Тестирование")
    kid = await _user(db, "student")
    adult = await _user(db, "student")
    await db.execute(
        text("INSERT INTO user_schedule_group (user_id, group_id) VALUES (:u, :g)"),
        {"u": adult, "g": adults},
    )
    await db.commit()
    return {
        "teacher": teacher, "adults": adults, "kid": kid, "adult": adult,
        "kid_slot": await _slot(db, teacher, MONDAY),
        "adult_slot": await _slot(db, teacher, FRIDAY, adults),
    }


@pytest.mark.asyncio
async def test_bookable_shows_only_own_group(db, scene):
    kid_view = {s.slot_id for s in (await schedule_booking_service.get_bookable(db, scene["kid"]))["slots"]}
    adult_view = {s.slot_id for s in (await schedule_booking_service.get_bookable(db, scene["adult"]))["slots"]}
    assert scene["kid_slot"] in kid_view and scene["adult_slot"] not in kid_view
    # Взрослый слот вне детской сетки всё равно виден взрослому.
    assert scene["adult_slot"] in adult_view and scene["kid_slot"] not in adult_view


@pytest.mark.asyncio
async def test_join_foreign_group_slot_is_404(db, scene):
    await _fill_pref(db, scene["kid"])
    await _fill_pref(db, scene["adult"])
    for student, slot in ((scene["kid"], scene["adult_slot"]), (scene["adult"], scene["kid_slot"])):
        with pytest.raises(DomainError) as exc:
            await schedule_booking_service.join_slot(db, student, slot)
        assert exc.value.status_code == 404
        await db.rollback()
    await schedule_booking_service.join_slot(db, scene["adult"], scene["adult_slot"])


@pytest.mark.asyncio
async def test_free_slots_default_group_and_explicit(db, scene):
    default_hours = {(s["weekday"], s["start_time"]) for s in (await schedule_booking_service.get_free_slots(db))["slots"]}
    adult_hours = {
        (s["weekday"], s["start_time"])
        for s in (await schedule_booking_service.get_free_slots(db, group_id=scene["adults"]))["slots"]
    }
    assert (FRIDAY, time(12, 0)) not in default_hours
    assert adult_hours == {(FRIDAY, time(12, 0))}


async def _occurrence(db, slot_id: int, teacher_id: int, at: datetime, student_id: int) -> int:
    occ = LessonOccurrence(slot_id=slot_id, teacher_id=teacher_id, scheduled_at=at, duration_minutes=60)
    db.add(occ)
    await db.flush()
    db.add(LessonOccurrenceParticipant(occurrence_id=occ.id, student_id=student_id, status="scheduled"))
    occ_id = occ.id
    await db.commit()
    return occ_id


@pytest.mark.asyncio
async def test_reschedule_candidates_and_accept_follow_group(db, scene):
    adult_occ = await _occurrence(db, scene["adult_slot"], scene["teacher"], _next_at(FRIDAY), scene["adult"])
    kid_occ = await _occurrence(db, scene["kid_slot"], scene["teacher"], _next_at(MONDAY), scene["kid"])

    adult_opts = await lesson_occurrence_service.list_available_slots(
        db, occurrence_id=adult_occ, student_id=scene["adult"], horizon_days=21,
    )
    kid_opts = await lesson_occurrence_service.list_available_slots(
        db, occurrence_id=kid_occ, student_id=scene["kid"], horizon_days=21,
    )
    assert adult_opts and all(o.astimezone(MSK).weekday() == FRIDAY for o in adult_opts)
    assert kid_opts and all(o.astimezone(MSK).weekday() == MONDAY for o in kid_opts)

    with pytest.raises(DomainError) as exc:
        await lesson_occurrence_service.reschedule_occurrence(
            db, occurrence_id=adult_occ, student_id=scene["adult"], new_scheduled_at=_next_at(MONDAY),
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_ad_hoc_student_bound_teacher_free(db, scene):
    with pytest.raises(DomainError) as exc:
        await lesson_occurrence_service.create_ad_hoc_occurrence(
            db, student_id=scene["adult"], teacher_id=scene["teacher"],
            scheduled_at=_next_at(MONDAY), duration_minutes=60,
        )
    assert exc.value.status_code == 422
    await db.rollback()
    occ, _ = await lesson_occurrence_service.create_ad_hoc_occurrence(
        db, student_id=scene["adult"], teacher_id=scene["teacher"],
        scheduled_at=_next_at(MONDAY), duration_minutes=60, require_scheduled_slot=False,
    )
    assert occ.id


@pytest.mark.asyncio
async def test_join_and_bookable_occurrences_follow_group(db, scene):
    kid_occ = await _occurrence(db, scene["kid_slot"], scene["teacher"], _next_at(MONDAY), scene["kid"])
    offered = await lesson_occurrence_service.list_bookable_occurrences_for_student(
        db, student_id=scene["adult"], teacher_ids=[scene["teacher"]],
    )
    assert kid_occ not in {o.id for o, _names in offered}
    with pytest.raises(DomainError) as exc:
        await lesson_occurrence_service.join_occurrence_as_student(
            db, occurrence_id=kid_occ, student_id=scene["adult"],
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_teachers_for_time_follow_group(db, scene):
    at = _next_at(MONDAY)
    kids = await lesson_calendar_service.list_teachers_for_time(db, scheduled_at=at, group_ids=[await _gid(db, "Дети · Информатика")])
    adults = await lesson_calendar_service.list_teachers_for_time(db, scheduled_at=at, group_ids=[scene["adults"]])
    assert scene["teacher"] in {t.id for t in kids}
    assert scene["teacher"] not in {t.id for t in adults}


def test_group_condition_lives_only_in_policy():
    """Сторож: сравнение группы слота с группами ученика — только через
    `schedule_group_service.slot_visible`, копии условия в сервисах запрещены."""
    services = Path(__file__).resolve().parents[1] / "app" / "services"
    offenders = []
    for path in services.glob("*.py"):
        if path.name == "schedule_group_service.py":
            continue
        src = path.read_text(encoding="utf-8")
        if re.search(r"(?<!for )\w*group_id\]?\s+(not\s+)?in\s+\w*group_ids", src):
            offenders.append(path.name)
    assert offenders == []


@pytest.mark.asyncio
async def test_inactive_group_falls_back_to_default(db, scene):
    """Выключенная группа не держит ученика: он видит группу по умолчанию."""
    from app.services import schedule_group_service

    gid = (await db.execute(text(
        "INSERT INTO schedule_group (audience, subject, name) VALUES ('adults', 'Архив', :n) RETURNING id"
    ), {"n": f"tsk1124 архив {random.randint(1, 10**6)}"})).scalar_one()
    student = await _user(db, "student")
    await db.execute(text("INSERT INTO user_schedule_group (user_id, group_id) VALUES (:u, :g)"), {"u": student, "g": gid})
    await db.execute(text("UPDATE schedule_group SET is_active = false WHERE id = :g"), {"g": gid})
    await db.commit()
    assert await schedule_group_service.effective_group_ids(db, student) == [
        await schedule_group_service.default_group_id(db)
    ]


@pytest.mark.asyncio
async def test_slotless_occurrence_belongs_to_default_group(db, scene):
    """Разовое занятие без слота — группа по умолчанию: взрослому не видно."""
    occ = LessonOccurrence(slot_id=None, teacher_id=scene["teacher"], scheduled_at=_next_at(MONDAY) + timedelta(hours=3), duration_minutes=60)
    db.add(occ)
    await db.commit()
    offered = await lesson_occurrence_service.list_bookable_occurrences_for_student(
        db, student_id=scene["adult"], teacher_ids=[scene["teacher"]],
    )
    assert occ.id not in {o.id for o, _ in offered}
    kid_offered = await lesson_occurrence_service.list_bookable_occurrences_for_student(
        db, student_id=scene["kid"], teacher_ids=[scene["teacher"]],
    )
    assert occ.id in {o.id for o, _ in kid_offered}


@pytest.mark.asyncio
async def test_own_foreign_slot_stays_in_mine(db, scene):
    """Ученик, уже прикреплённый к слоту чужой группы, видит его среди своих."""
    await db.execute(
        text("INSERT INTO lesson_slot_student (slot_id, student_id, is_active) VALUES (:s, :u, true)"),
        {"s": scene["kid_slot"], "u": scene["adult"]},
    )
    await db.commit()
    data = await schedule_booking_service.get_bookable(db, scene["adult"])
    assert scene["kid_slot"] in {s.slot_id for s in data["my_slots"]}


@pytest.mark.asyncio
async def test_free_slots_unknown_group_is_error(db, scene):
    with pytest.raises(DomainError) as exc:
        await schedule_booking_service.get_free_slots(db, group_id=999_999)
    assert exc.value.status_code == 404
