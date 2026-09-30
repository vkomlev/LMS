"""tsk-1170: расхождения расписания ученика с его последним пожеланием.

Правило выведено по данным прода 30.09 (см. `get_mismatches`):
слот вне выбранных часов, не то число занятий, ответил без слотов — только
для тарифа с занятиями; «пожелание новее вёрстки» — пометка, не причина.
"""
from __future__ import annotations

import random
from datetime import date, time

from sqlalchemy import text

from app.models.users import Users
from app.schemas.schedule_preference import SchedulePreferenceWrite
from app.services import schedule_preference_service
from app.services.auth.session_service import create_session


async def _user(db, role: str, prefix: str) -> int:
    u = Users(
        email=f"{prefix}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name=f"{prefix}-user", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, id FROM roles WHERE name = :r ON CONFLICT DO NOTHING"
        ),
        {"u": u.id, "r": role},
    )
    await db.commit()
    return u.id


async def _plan(db, student_id: int, code: str) -> None:
    await db.execute(
        text(
            "INSERT INTO student_subscription (student_id, plan_id, starts_on) "
            "SELECT :s, id, CURRENT_DATE FROM subscription_plan WHERE code = :c"
        ),
        {"s": student_id, "c": code},
    )
    await db.commit()


async def _slot(db, teacher_id: int, student_id: int, weekday: int, hhmm: str,
                active_until: str | None = None) -> int:
    slot_id = (
        await db.execute(
            text(
                "INSERT INTO lesson_slot (teacher_id, weekday, start_time, duration_minutes, "
                "active_until) VALUES (:t, :w, :s, 60, :u) "
                "RETURNING id"
            ),
            {"t": teacher_id, "w": weekday, "s": time.fromisoformat(hhmm),
             "u": date.fromisoformat(active_until) if active_until else None},
        )
    ).scalar_one()
    await db.execute(
        text("INSERT INTO lesson_slot_student (slot_id, student_id) VALUES (:sl, :st)"),
        {"sl": slot_id, "st": student_id},
    )
    await db.commit()
    return slot_id


async def _prefer(db, student_id: int, lessons: int, hours: list[tuple[int, str, str]]) -> None:
    await schedule_preference_service.save_preference(
        db,
        student_id,
        SchedulePreferenceWrite(
            lessons_per_week=lessons,
            hours=[{"weekday": w, "start_time": t, "kind": k} for w, t, k in hours],
        ),
        changed_by=student_id,
    )


async def _rows(db) -> dict[int, dict]:
    result = await schedule_preference_service.get_mismatches(db)
    assert result["total"] == len(result["students"])
    return {r["student_id"]: r for r in result["students"]}


async def test_slot_outside_wishes_is_mismatch(db):
    teacher = await _user(db, "teacher", "tsk1170-t")
    sid = await _user(db, "student", "tsk1170-out")
    await _plan(db, sid, "base")
    await _prefer(db, sid, 2, [(1, "17:00", "preferred"), (5, "10:00", "preferred")])
    await _slot(db, teacher, sid, 1, "17:00")
    await _slot(db, teacher, sid, 5, "11:00")

    row = (await _rows(db))[sid]
    assert row["reasons"] == ["outside_windows"]
    by_time = {(s["weekday"], s["start_time"]): s["in_wishes"] for s in row["slots"]}
    assert by_time == {(1, "17:00"): True, (5, "11:00"): False}
    assert row["answered_at"] is not None


async def test_possible_hour_counts_as_wish_and_fit_is_silent(db):
    """Слот в «возможном» часе — не расхождение; всё совпало — ученика нет в списке."""
    teacher = await _user(db, "teacher", "tsk1170-t")
    sid = await _user(db, "student", "tsk1170-fit")
    await _plan(db, sid, "base")
    await _prefer(db, sid, 2, [(0, "15:00", "preferred"), (2, "16:00", "possible"),
                               (2, "15:00", "preferred")])
    await _slot(db, teacher, sid, 0, "15:00")
    await _slot(db, teacher, sid, 2, "16:00")

    assert sid not in await _rows(db)


async def test_count_mismatch_and_ended_slot_ignored(db):
    teacher = await _user(db, "teacher", "tsk1170-t")
    sid = await _user(db, "student", "tsk1170-cnt")
    await _plan(db, sid, "base")
    await _prefer(db, sid, 2, [(0, "15:00", "preferred"), (2, "15:00", "preferred")])
    await _slot(db, teacher, sid, 0, "15:00")
    # Закончившийся слот — не «что стоит сейчас».
    await _slot(db, teacher, sid, 2, "15:00", active_until="2020-01-01")

    row = (await _rows(db))[sid]
    assert row["reasons"] == ["count_mismatch"]
    assert len(row["slots"]) == 1


async def test_no_slots_only_for_plan_with_lessons(db):
    """Выпускник с анкетой и без слотов — не расхождение (так на проде все 9)."""
    active = await _user(db, "student", "tsk1170-noslot")
    await _plan(db, active, "base")
    await _prefer(db, active, 1, [(3, "12:00", "preferred")])
    gone = await _user(db, "student", "tsk1170-alumni")
    await _plan(db, gone, "alumni")
    await _prefer(db, gone, 1, [(3, "12:00", "preferred")])
    selfp = await _user(db, "student", "tsk1170-self")
    await _plan(db, selfp, "self")
    await _prefer(db, selfp, 1, [(3, "12:00", "preferred")])

    rows = await _rows(db)
    assert rows[active]["reasons"] == ["no_slots"]
    assert gone not in rows
    assert selfp not in rows


async def test_manual_ack_without_slots_is_mismatch(db):
    methodist = await _user(db, "methodist", "tsk1170-m")
    sid = await _user(db, "student", "tsk1170-ack")
    await _plan(db, sid, "base")
    await schedule_preference_service.acknowledge_preference(db, sid, acknowledged_by=methodist)

    row = (await _rows(db))[sid]
    assert row["reasons"] == ["no_slots"]
    assert row["acknowledged_manually"] is True
    assert row["hours"] == []


async def test_preference_newer_flag(db):
    teacher = await _user(db, "teacher", "tsk1170-t")
    sid = await _user(db, "student", "tsk1170-new")
    await _plan(db, sid, "base")
    await _slot(db, teacher, sid, 1, "15:00")
    await _slot(db, teacher, sid, 3, "15:00")
    # Вёрстка была вчера: внутри тестовой транзакции now() у всех строк один.
    await db.execute(
        text(
            "UPDATE lesson_slot_student SET created_at = now() - interval '1 day' "
            "WHERE student_id = :s"
        ),
        {"s": sid},
    )
    await db.execute(
        text(
            "UPDATE lesson_slot SET updated_at = now() - interval '1 day' WHERE id IN "
            "(SELECT slot_id FROM lesson_slot_student WHERE student_id = :s)"
        ),
        {"s": sid},
    )
    await db.commit()
    # Анкета принимает только часы, где занятие есть (tsk-746): 17:00 ведут у соседа.
    other = await _user(db, "student", "tsk1170-other")
    await _plan(db, other, "base")
    await _slot(db, teacher, other, 1, "17:00")
    await _slot(db, teacher, other, 3, "17:00")
    # Анкета после вёрстки и мимо неё — пожелание не отработано.
    await _prefer(db, sid, 2, [(1, "17:00", "preferred"), (3, "17:00", "preferred")])

    row = (await _rows(db))[sid]
    assert row["reasons"] == ["outside_windows"]
    assert row["preference_newer"] is True


async def test_endpoint_gate_and_shape(db, client):
    methodist = await _user(db, "methodist", "tsk1170-m")
    student = await _user(db, "student", "tsk1170-gate")
    await _plan(db, student, "base")
    await _prefer(db, student, 1, [(3, "12:00", "preferred")])

    token, _, _ = await create_session(db, user_id=methodist)
    resp = await client.get(
        "/api/v1/methodist/schedule-preferences/mismatches",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    row = next(r for r in body["students"] if r["student_id"] == student)
    assert row["reasons"] == ["no_slots"]
    assert row["hours"] == [{"weekday": 3, "start_time": "12:00", "kind": "preferred"}]
    assert row["group_ids"]

    s_token, _, _ = await create_session(db, user_id=student)
    denied = await client.get(
        "/api/v1/methodist/schedule-preferences/mismatches",
        headers={"Authorization": f"Bearer {s_token}"},
    )
    assert denied.status_code == 403
