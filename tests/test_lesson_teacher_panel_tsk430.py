"""tsk-430/435 (Календарь LMS): панель преподавателя, ad-hoc, reschedule, группы.

Покрывает:
- `GET /teacher/lesson-occurrences`: ownership 403, живой флаг `is_overdue`
  на КАЖДОГО участника (только для `status='scheduled'`, не для
  `confirmed`/будущих).
- `POST /teacher/lesson-occurrences/{id}/attendance`: manual_present/absent
  на конкретного участника, 409 на `rescheduled`.
- `POST /teacher/lesson-occurrences/add-student`: создание ad-hoc + участник,
  403 teacher-mismatch, 422 неверная роль, 409 коллизия (по УЧЕНИКУ, не
  преподавателю — групповая модель tsk-435).
- `GET /lesson-occurrences/available-slots`, `POST .../reschedule`,
  `POST .../ad-hoc`: часы работы школы, коллизии по ученику, перенос
  помечает старое УЧАСТИЕ `rescheduled` и создаёт новый occurrence.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from app.models.operating_hours import OperatingHours
from app.models.users import Users
from app.services.auth.session_service import create_session

MSK = ZoneInfo("Europe/Moscow")


# ============================== Helpers ==============================


async def _create_user(db, *, role: str | None = None, prefix: str = "tsk430") -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=f"{prefix}-user", tg_id=None)
    db.add(u)
    await db.flush()
    if role:
        r = await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role})
        row = r.fetchone()
        if row is None:
            await db.execute(
                text("INSERT INTO roles (name) VALUES (:n) ON CONFLICT DO NOTHING"),
                {"n": role},
            )
            r = await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role})
            row = r.fetchone()
        role_id = int(row[0])
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "VALUES (:u, :r) ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "r": role_id},
        )
    await db.commit()
    return u.id


async def _create_occurrence_with_participant(
    db, *, student_id: int, teacher_id: int, scheduled_at: datetime,
    status: str = "scheduled", duration_minutes: int = 60,
) -> int:
    occ = LessonOccurrence(
        slot_id=None, teacher_id=teacher_id, scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
    )
    db.add(occ)
    await db.flush()
    db.add(LessonOccurrenceParticipant(occurrence_id=occ.id, student_id=student_id, status=status))
    occ_id = occ.id
    await db.commit()
    return occ_id


async def _set_operating_hours_for_weekday_of(db, day: date, *, start=time(9, 0), end=time(21, 0)) -> None:
    """Ставит operating_hours на weekday конкретной календарной даты (MSK)."""
    row = OperatingHours(
        weekday=day.weekday(), start_time=start, end_time=end, timezone="Europe/Moscow",
    )
    db.add(row)
    await db.commit()


def _next_day_with_operating_hours_seeded() -> date:
    """Ближайший день (>= завтра), для которого удобно ставить operating_hours."""
    return (datetime.now(dt_timezone.utc) + timedelta(days=1)).date()


# ============================== Teacher list + is_overdue ==============================


@pytest.mark.asyncio
async def test_teacher_list_403_for_other_teacher(db, client):
    teacher_a = await _create_user(db, role="teacher", prefix="tsk430-teachA")
    teacher_b = await _create_user(db, role="teacher", prefix="tsk430-teachB")
    token_b, _, _ = await create_session(db, user_id=teacher_b)

    resp = await client.get(
        "/api/v1/teacher/lesson-occurrences",
        params={"teacher_id": teacher_a},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_teacher_list_is_overdue_only_for_scheduled_past(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk430-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    token, _, _ = await create_session(db, user_id=teacher_id)

    overdue_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) - timedelta(minutes=30),
        status="scheduled",
    )
    confirmed_past_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) - timedelta(minutes=30),
        status="confirmed",
    )
    future_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=2),
        status="scheduled",
    )

    resp = await client.get(
        "/api/v1/teacher/lesson-occurrences",
        params={"teacher_id": teacher_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    by_id = {item["id"]: item["participants"][0]["is_overdue"] for item in resp.json()}
    assert by_id[overdue_id] is True
    assert by_id[confirmed_past_id] is False
    assert by_id[future_id] is False


@pytest.mark.asyncio
async def test_teacher_list_group_occurrence_has_all_participants(db, client):
    student_a = await _create_user(db, role="student", prefix="tsk430-stuA")
    student_b = await _create_user(db, role="student", prefix="tsk430-stuB")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    token, _, _ = await create_session(db, user_id=teacher_id)

    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_a, teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=1),
    )
    db.add(LessonOccurrenceParticipant(occurrence_id=occ_id, student_id=student_b, status="scheduled"))
    await db.commit()

    resp = await client.get(
        "/api/v1/teacher/lesson-occurrences",
        params={"teacher_id": teacher_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    item = next(i for i in resp.json() if i["id"] == occ_id)
    assert {p["student_id"] for p in item["participants"]} == {student_a, student_b}


@pytest.mark.asyncio
async def test_teacher_list_participant_order_stable_after_attendance_update(db, client):
    """tsk-441: список участников не должен "прыгать" после отметки явки —
    без ORDER BY Postgres не гарантирует порядок строк после UPDATE, и
    учитель кликает по позиции, а не по конкретному человеку."""
    student_a = await _create_user(db, role="student", prefix="tsk441-stuA")
    student_b = await _create_user(db, role="student", prefix="tsk441-stuB")
    student_c = await _create_user(db, role="student", prefix="tsk441-stuC")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk441-teach")
    token, _, _ = await create_session(db, user_id=teacher_id)

    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_a, teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=1),
    )
    db.add(LessonOccurrenceParticipant(occurrence_id=occ_id, student_id=student_b, status="scheduled"))
    db.add(LessonOccurrenceParticipant(occurrence_id=occ_id, student_id=student_c, status="scheduled"))
    await db.commit()

    async def _order() -> list[int]:
        resp = await client.get(
            "/api/v1/teacher/lesson-occurrences",
            params={"teacher_id": teacher_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        item = next(i for i in resp.json() if i["id"] == occ_id)
        return [p["student_id"] for p in item["participants"]]

    before = await _order()
    assert before == [student_a, student_b, student_c]

    resp = await client.post(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/attendance",
        params={"teacher_id": teacher_id},
        json={"student_id": student_b, "action": "manual_present"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    after = await _order()
    assert after == before


# ============================== Teacher manual attendance ==============================


@pytest.mark.asyncio
async def test_teacher_manual_present_confirms(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk430-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    token, _, _ = await create_session(db, user_id=teacher_id)

    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) - timedelta(minutes=15),
        status="scheduled",
    )

    resp = await client.post(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/attendance",
        params={"teacher_id": teacher_id},
        json={"student_id": student_id, "action": "manual_present"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "confirmed"


@pytest.mark.asyncio
async def test_teacher_manual_absent_overrides_existing_no_show(db, client):
    """Преподаватель может исправить ошибочный no_show вручную (не заблокировано)."""
    student_id = await _create_user(db, role="student", prefix="tsk430-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    token, _, _ = await create_session(db, user_id=teacher_id)

    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) - timedelta(minutes=30),
        status="no_show",
    )

    resp = await client.post(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/attendance",
        params={"teacher_id": teacher_id},
        json={"student_id": student_id, "action": "manual_present"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "confirmed"


@pytest.mark.asyncio
async def test_teacher_attendance_409_when_rescheduled(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk430-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    token, _, _ = await create_session(db, user_id=teacher_id)

    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=1),
        status="rescheduled",
    )

    resp = await client.post(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/attendance",
        params={"teacher_id": teacher_id},
        json={"student_id": student_id, "action": "manual_present"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_teacher_attendance_404_for_non_participant(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk430-stud")
    other_student_id = await _create_user(db, role="student", prefix="tsk430-other")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    token, _, _ = await create_session(db, user_id=teacher_id)

    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=1),
    )

    resp = await client.post(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/attendance",
        params={"teacher_id": teacher_id},
        json={"student_id": other_student_id, "action": "manual_present"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, resp.text


# ============================== add-student ==============================


@pytest.mark.asyncio
async def test_add_student_creates_ad_hoc_occurrence(db, client):
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    student_id = await _create_user(db, role="student", prefix="tsk430-stud")
    token, _, _ = await create_session(db, user_id=teacher_id)

    resp = await client.post(
        "/api/v1/teacher/lesson-occurrences/add-student",
        json={
            "teacher_id": teacher_id,
            "student_id": student_id,
            "scheduled_at": (datetime.now(dt_timezone.utc) + timedelta(hours=3)).isoformat(),
            "duration_minutes": 45,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["slot_id"] is None
    assert body["teacher_id"] == teacher_id

    participant_row = (
        await db.execute(
            text(
                "SELECT status FROM lesson_occurrence_participant "
                "WHERE occurrence_id = :oid AND student_id = :sid"
            ),
            {"oid": body["id"], "sid": student_id},
        )
    ).fetchone()
    assert participant_row is not None
    assert participant_row[0] == "scheduled"


@pytest.mark.asyncio
async def test_add_student_403_teacher_mismatch(db, client):
    teacher_a = await _create_user(db, role="teacher", prefix="tsk430-teachA")
    teacher_b = await _create_user(db, role="teacher", prefix="tsk430-teachB")
    student_id = await _create_user(db, role="student", prefix="tsk430-stud")
    token_b, _, _ = await create_session(db, user_id=teacher_b)

    resp = await client.post(
        "/api/v1/teacher/lesson-occurrences/add-student",
        json={
            "teacher_id": teacher_a,
            "student_id": student_id,
            "scheduled_at": (datetime.now(dt_timezone.utc) + timedelta(hours=3)).isoformat(),
            "duration_minutes": 45,
        },
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_add_student_422_wrong_role(db, client):
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    not_a_student = await _create_user(db, prefix="tsk430-plain")
    token, _, _ = await create_session(db, user_id=teacher_id)

    resp = await client.post(
        "/api/v1/teacher/lesson-occurrences/add-student",
        json={
            "teacher_id": teacher_id,
            "student_id": not_a_student,
            "scheduled_at": (datetime.now(dt_timezone.utc) + timedelta(hours=3)).isoformat(),
            "duration_minutes": 45,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_add_student_no_conflict_different_students_same_teacher_time(db, client):
    """Групповая модель (tsk-435): ДРУГОЙ ученик на то же время того же
    преподавателя больше НЕ конфликтует — это и есть группа."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    student_a = await _create_user(db, role="student", prefix="tsk430-studA")
    student_b = await _create_user(db, role="student", prefix="tsk430-studB")
    token, _, _ = await create_session(db, user_id=teacher_id)

    scheduled_at = datetime.now(dt_timezone.utc) + timedelta(hours=5)
    await _create_occurrence_with_participant(
        db, student_id=student_a, teacher_id=teacher_id, scheduled_at=scheduled_at,
        duration_minutes=60,
    )

    resp = await client.post(
        "/api/v1/teacher/lesson-occurrences/add-student",
        json={
            "teacher_id": teacher_id,
            "student_id": student_b,
            "scheduled_at": (scheduled_at + timedelta(minutes=30)).isoformat(),
            "duration_minutes": 60,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_add_student_409_collision_same_student(db, client):
    """Коллизия теперь по УЧЕНИКУ: тот же ученик не может быть в двух местах одновременно."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    student_id = await _create_user(db, role="student", prefix="tsk430-stud")
    token, _, _ = await create_session(db, user_id=teacher_id)

    scheduled_at = datetime.now(dt_timezone.utc) + timedelta(hours=5)
    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id, scheduled_at=scheduled_at,
        duration_minutes=60,
    )

    resp = await client.post(
        "/api/v1/teacher/lesson-occurrences/add-student",
        json={
            "teacher_id": teacher_id,
            "student_id": student_id,
            "scheduled_at": (scheduled_at + timedelta(minutes=30)).isoformat(),
            "duration_minutes": 60,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409, resp.text


# ============================== add-participant to existing occurrence ==============================


@pytest.mark.asyncio
async def test_add_participant_to_existing_occurrence(db, client):
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    student_a = await _create_user(db, role="student", prefix="tsk430-studA")
    student_b = await _create_user(db, role="student", prefix="tsk430-studB")
    token, _, _ = await create_session(db, user_id=teacher_id)

    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_a, teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=2),
    )

    resp = await client.post(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/participants",
        params={"teacher_id": teacher_id},
        json={"student_id": student_b},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["student_id"] == student_b

    count = (
        await db.execute(
            text("SELECT COUNT(*) FROM lesson_occurrence_participant WHERE occurrence_id = :oid"),
            {"oid": occ_id},
        )
    ).scalar()
    assert count == 2


# ============================== available-slots / reschedule / ad-hoc ==============================


@pytest.mark.asyncio
async def test_available_slots_empty_without_operating_hours(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk430-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    token, _, _ = await create_session(db, user_id=student_id)

    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=1),
    )

    resp = await client.get(
        "/api/v1/lesson-occurrences/available-slots",
        params={"occurrence_id": occ_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


@pytest.mark.asyncio
async def test_available_slots_within_operating_hours_no_collision(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk430-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    token, _, _ = await create_session(db, user_id=student_id)

    target_day = _next_day_with_operating_hours_seeded()
    await _set_operating_hours_for_weekday_of(db, target_day)

    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=1),
        duration_minutes=60,
    )

    resp = await client.get(
        "/api/v1/lesson-occurrences/available-slots",
        params={"occurrence_id": occ_id, "limit": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    candidates = resp.json()
    assert len(candidates) > 0
    for item in candidates:
        dt = datetime.fromisoformat(item["scheduled_at"])
        local = dt.astimezone(MSK)
        assert local.weekday() == target_day.weekday()
        assert time(9, 0) <= local.time() < time(21, 0)


@pytest.mark.asyncio
async def test_reschedule_creates_new_marks_old_participant_rescheduled(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk430-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    token, _, _ = await create_session(db, user_id=student_id)

    target_day = _next_day_with_operating_hours_seeded()
    await _set_operating_hours_for_weekday_of(db, target_day)

    old_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=1),
        duration_minutes=60,
    )

    new_local = datetime.combine(target_day, time(11, 0), tzinfo=MSK)
    new_utc = new_local.astimezone(dt_timezone.utc)

    resp = await client.post(
        f"/api/v1/lesson-occurrences/{old_id}/reschedule",
        json={"new_scheduled_at": new_utc.isoformat()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    new_occ = resp.json()
    assert new_occ["my_status"] == "scheduled"
    assert new_occ["id"] != old_id

    old_row = (
        await db.execute(
            text(
                "SELECT status, rescheduled_to_occurrence_id FROM lesson_occurrence_participant "
                "WHERE occurrence_id = :oid AND student_id = :sid"
            ),
            {"oid": old_id, "sid": student_id},
        )
    ).fetchone()
    assert old_row[0] == "rescheduled"
    assert old_row[1] == new_occ["id"]


@pytest.mark.asyncio
async def test_reschedule_does_not_affect_other_group_participants(db, client):
    student_a = await _create_user(db, role="student", prefix="tsk430-stuA")
    student_b = await _create_user(db, role="student", prefix="tsk430-stuB")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    token_a, _, _ = await create_session(db, user_id=student_a)

    target_day = _next_day_with_operating_hours_seeded()
    await _set_operating_hours_for_weekday_of(db, target_day)

    old_id = await _create_occurrence_with_participant(
        db, student_id=student_a, teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=1),
        duration_minutes=60,
    )
    db.add(LessonOccurrenceParticipant(occurrence_id=old_id, student_id=student_b, status="scheduled"))
    await db.commit()

    new_local = datetime.combine(target_day, time(11, 0), tzinfo=MSK)
    resp = await client.post(
        f"/api/v1/lesson-occurrences/{old_id}/reschedule",
        json={"new_scheduled_at": new_local.astimezone(dt_timezone.utc).isoformat()},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 201, resp.text

    b_status = (
        await db.execute(
            text(
                "SELECT status FROM lesson_occurrence_participant "
                "WHERE occurrence_id = :oid AND student_id = :sid"
            ),
            {"oid": old_id, "sid": student_b},
        )
    ).scalar()
    assert b_status == "scheduled", "Перенос участника A не должен трогать участника B"


@pytest.mark.asyncio
async def test_reschedule_422_outside_operating_hours(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk430-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    token, _, _ = await create_session(db, user_id=student_id)

    target_day = _next_day_with_operating_hours_seeded()
    await _set_operating_hours_for_weekday_of(db, target_day, start=time(9, 0), end=time(21, 0))

    old_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=1),
        duration_minutes=60,
    )

    # 23:00 MSK — вне часов работы 09:00-21:00
    new_local = datetime.combine(target_day, time(23, 0), tzinfo=MSK)
    resp = await client.post(
        f"/api/v1/lesson-occurrences/{old_id}/reschedule",
        json={"new_scheduled_at": new_local.astimezone(dt_timezone.utc).isoformat()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_ad_hoc_creates_occurrence_within_operating_hours(db, client):
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    student_id = await _create_user(db, role="student", prefix="tsk430-stud")
    token, _, _ = await create_session(db, user_id=student_id)

    target_day = _next_day_with_operating_hours_seeded()
    await _set_operating_hours_for_weekday_of(db, target_day)
    scheduled_local = datetime.combine(target_day, time(12, 0), tzinfo=MSK)

    resp = await client.post(
        "/api/v1/lesson-occurrences/ad-hoc",
        json={
            "teacher_id": teacher_id,
            "scheduled_at": scheduled_local.astimezone(dt_timezone.utc).isoformat(),
            "duration_minutes": 60,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["my_status"] == "scheduled"
    assert body["teacher_id"] == teacher_id
    assert body["slot_id"] is None


@pytest.mark.asyncio
async def test_ad_hoc_409_collision(db, client):
    teacher_id = await _create_user(db, role="teacher", prefix="tsk430-teach")
    student_id = await _create_user(db, role="student", prefix="tsk430-stud")
    token, _, _ = await create_session(db, user_id=student_id)

    scheduled_at = datetime.now(dt_timezone.utc) + timedelta(hours=2)
    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id, scheduled_at=scheduled_at,
        duration_minutes=60,
    )

    resp = await client.post(
        "/api/v1/lesson-occurrences/ad-hoc",
        json={
            "teacher_id": teacher_id,
            "scheduled_at": (scheduled_at + timedelta(minutes=15)).isoformat(),
            "duration_minutes": 30,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409, resp.text
