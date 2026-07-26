"""tsk-429/435 (Календарь LMS): явка ученика + reminder/no-show cron, по участнику.

Покрывает:
- `POST /lesson-occurrences/{id}/attendance`: 200 joined/declined, 403 IDOR
  (ученик не входит в участники), 404 (occurrence не существует), 409 (уже
  закрытый статус участия).
- `GET /me/lesson-occurrences`: скоуп по текущему ученику (через участие),
  `from`/`to` фильтры.
- `lesson_attendance_cron_tick`: reminder once-only на КАЖДОГО участника
  отдельно (не дублирует и не гасит соседей в групповом occurrence),
  no_show только для участника в `status='scheduled'` (не трогает
  `confirmed`), создаёт уведомления студенту И учителю.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from app.models.users import Users
from app.services.auth.session_service import create_session
from app.services.lesson_attendance_cron_service import lesson_attendance_cron_tick


# ============================== Helpers ==============================


async def _create_user(db, *, role: str | None = None, prefix: str = "tsk429") -> int:
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
) -> tuple[int, int]:
    """Возвращает (occurrence_id, participant_id)."""
    occ = LessonOccurrence(
        slot_id=None, teacher_id=teacher_id, scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
    )
    db.add(occ)
    await db.flush()
    participant = LessonOccurrenceParticipant(
        occurrence_id=occ.id, student_id=student_id, status=status,
    )
    db.add(participant)
    await db.flush()
    occ_id, participant_id = occ.id, participant.id
    await db.commit()
    return occ_id, participant_id


# ============================== Attendance API ==============================


@pytest.mark.asyncio
async def test_attendance_joined_confirms_and_logs_event(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk429-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk429-teach")
    student_token, _, _ = await create_session(db, user_id=student_id)

    occ_id, _pid = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    resp = await client.post(
        f"/api/v1/lesson-occurrences/{occ_id}/attendance",
        json={"action": "joined"},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["my_status"] == "confirmed"

    row = (
        await db.execute(
            text(
                "SELECT action, actor_user_id FROM attendance_event "
                "WHERE occurrence_id = :oid"
            ),
            {"oid": occ_id},
        )
    ).fetchone()
    assert row is not None
    assert row[0] == "joined"
    assert row[1] == student_id


@pytest.mark.asyncio
async def test_attendance_declined_sets_status(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk429-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk429-teach")
    student_token, _, _ = await create_session(db, user_id=student_id)

    occ_id, _pid = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )

    resp = await client.post(
        f"/api/v1/lesson-occurrences/{occ_id}/attendance",
        json={"action": "declined"},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["my_status"] == "declined"


@pytest.mark.asyncio
async def test_attendance_403_for_non_participant(db, client):
    """IDOR: student B не входит в участники occurrence student A."""
    student_a = await _create_user(db, role="student", prefix="tsk429-stuA")
    student_b = await _create_user(db, role="student", prefix="tsk429-stuB")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk429-teach")
    token_b, _, _ = await create_session(db, user_id=student_b)

    occ_id, _pid = await _create_occurrence_with_participant(
        db, student_id=student_a, teacher_id=teacher_id,
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    resp = await client.post(
        f"/api/v1/lesson-occurrences/{occ_id}/attendance",
        json={"action": "joined"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_attendance_404_for_missing_occurrence(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk429-stud")
    token, _, _ = await create_session(db, user_id=student_id)

    resp = await client.post(
        "/api/v1/lesson-occurrences/999999999/attendance",
        json={"action": "joined"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_attendance_409_when_already_closed(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk429-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk429-teach")
    token, _, _ = await create_session(db, user_id=student_id)

    occ_id, _pid = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(timezone.utc) - timedelta(hours=1),
        status="no_show",
    )

    resp = await client.post(
        f"/api/v1/lesson-occurrences/{occ_id}/attendance",
        json={"action": "joined"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_group_occurrence_attendance_independent_per_participant(db, client):
    """Групповое occurrence: один участник confirmed не влияет на статус другого."""
    student_a = await _create_user(db, role="student", prefix="tsk429-stuA")
    student_b = await _create_user(db, role="student", prefix="tsk429-stuB")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk429-teach")
    token_a, _, _ = await create_session(db, user_id=student_a)

    occ_id, _pid_a = await _create_occurrence_with_participant(
        db, student_id=student_a, teacher_id=teacher_id,
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    participant_b = LessonOccurrenceParticipant(
        occurrence_id=occ_id, student_id=student_b, status="scheduled",
    )
    db.add(participant_b)
    await db.commit()

    resp = await client.post(
        f"/api/v1/lesson-occurrences/{occ_id}/attendance",
        json={"action": "joined"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["my_status"] == "confirmed"

    b_status = (
        await db.execute(
            text(
                "SELECT status FROM lesson_occurrence_participant "
                "WHERE occurrence_id = :oid AND student_id = :sid"
            ),
            {"oid": occ_id, "sid": student_b},
        )
    ).scalar()
    assert b_status == "scheduled", "Участник B не должен измениться от действия участника A"


@pytest.mark.asyncio
async def test_list_my_occurrences_scoped_and_filtered(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk429-stud")
    other_student_id = await _create_user(db, role="student", prefix="tsk429-other")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk429-teach")
    token, _, _ = await create_session(db, user_id=student_id)

    now = datetime.now(timezone.utc)
    mine_soon, _ = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id, scheduled_at=now + timedelta(days=1)
    )
    mine_far, _ = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id, scheduled_at=now + timedelta(days=10)
    )
    _not_mine, _ = await _create_occurrence_with_participant(
        db, student_id=other_student_id, teacher_id=teacher_id, scheduled_at=now + timedelta(days=1)
    )

    resp = await client.get(
        "/api/v1/me/lesson-occurrences",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    ids = {item["id"] for item in resp.json()}
    assert ids == {mine_soon, mine_far}

    resp2 = await client.get(
        "/api/v1/me/lesson-occurrences",
        params={"to": (now + timedelta(days=5)).isoformat()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.status_code == 200, resp2.text
    ids2 = {item["id"] for item in resp2.json()}
    assert ids2 == {mine_soon}


# ============================== Cron: reminder + no-show ==============================


@pytest.mark.asyncio
async def test_reminder_sent_once_not_twice(db, db_session_factory):
    student_id = await _create_user(db, role="student", prefix="tsk429-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk429-teach")

    occ_id, _pid = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        status="scheduled",
    )

    summary1 = await lesson_attendance_cron_tick(db_session_factory)
    assert summary1["locked"] is True
    assert summary1["reminders_sent"] >= 1

    count1 = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM notifications "
                "WHERE kind = 'lesson_reminder' AND (payload->>'occurrence_id')::int = :oid"
            ),
            {"oid": occ_id},
        )
    ).scalar()
    assert count1 == 1

    summary2 = await lesson_attendance_cron_tick(db_session_factory)
    assert summary2["locked"] is True

    count2 = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM notifications "
                "WHERE kind = 'lesson_reminder' AND (payload->>'occurrence_id')::int = :oid"
            ),
            {"oid": occ_id},
        )
    ).scalar()
    assert count2 == 1, "Второй тик не должен дублировать напоминание"


@pytest.mark.asyncio
async def test_reminder_sent_to_each_group_participant_independently(db, db_session_factory):
    student_a = await _create_user(db, role="student", prefix="tsk429-stuA")
    student_b = await _create_user(db, role="student", prefix="tsk429-stuB")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk429-teach")

    occ_id, _pid_a = await _create_occurrence_with_participant(
        db, student_id=student_a, teacher_id=teacher_id,
        scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db.add(LessonOccurrenceParticipant(occurrence_id=occ_id, student_id=student_b, status="scheduled"))
    await db.commit()

    await lesson_attendance_cron_tick(db_session_factory)

    for sid in (student_a, student_b):
        count = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM notifications "
                    "WHERE kind = 'lesson_reminder' AND user_id = :uid "
                    "AND (payload->>'occurrence_id')::int = :oid"
                ),
                {"uid": sid, "oid": occ_id},
            )
        ).scalar()
        assert count == 1, f"Участник {sid} должен получить своё напоминание"


@pytest.mark.asyncio
async def test_no_show_marks_scheduled_past_threshold(db, db_session_factory):
    student_id = await _create_user(db, role="student", prefix="tsk429-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk429-teach")

    occ_id, participant_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        status="scheduled",
    )

    summary = await lesson_attendance_cron_tick(db_session_factory)
    assert summary["locked"] is True
    assert summary["no_show_marked"] >= 1

    row = (
        await db.execute(
            text("SELECT status FROM lesson_occurrence_participant WHERE id = :pid"),
            {"pid": participant_id},
        )
    ).fetchone()
    assert row[0] == "no_show"

    event = (
        await db.execute(
            text(
                "SELECT action, actor_user_id FROM attendance_event WHERE occurrence_id = :oid"
            ),
            {"oid": occ_id},
        )
    ).fetchone()
    assert event[0] == "auto_no_show"
    assert event[1] is None

    notif_count = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM notifications "
                "WHERE kind = 'lesson_missed' AND (payload->>'occurrence_id')::int = :oid"
            ),
            {"oid": occ_id},
        )
    ).scalar()
    assert notif_count == 2, "Уведомление и ученику, и преподавателю"


@pytest.mark.asyncio
async def test_no_show_does_not_touch_confirmed_participant(db, db_session_factory):
    """confirmed = ученик уже нажал «Я на занятии» — прошедшее время не должно
    задним числом переписать это в no_show."""
    student_id = await _create_user(db, role="student", prefix="tsk429-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk429-teach")

    _occ_id, participant_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        status="confirmed",
    )

    await lesson_attendance_cron_tick(db_session_factory)

    row = (
        await db.execute(
            text("SELECT status FROM lesson_occurrence_participant WHERE id = :pid"),
            {"pid": participant_id},
        )
    ).fetchone()
    assert row[0] == "confirmed"
