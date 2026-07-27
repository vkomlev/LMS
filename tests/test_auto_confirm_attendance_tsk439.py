"""tsk-439 (Календарь LMS): авто-подтверждение явки по реальному учебному
действию. Решение оператора: если у ученика прямо сейчас идёт занятие
(участие ещё `scheduled`, время в пределах [scheduled_at, scheduled_at+
duration)) и он совершает реальное учебное действие (сдача ответа на
задание / завершение материала) — явка подтверждается автоматически, без
явного клика "Я на занятии".

Покрывает:
- `LessonOccurrenceParticipantRepository.get_current_scheduled_for_student` —
  окно [start, start+duration), статус строго `scheduled`.
- `lesson_attendance_service.auto_confirm_if_in_progress` — confirms +
  attendance_event(action='auto_joined'); тихий no-op вне окна.
- `POST /learning/materials/{id}/complete` — реальный учебный триггер
  (интеграционно, endpoint не строится ради задания/попытки — это дешевле).
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from app.models.users import Users
from app.repos.lesson_calendar_repository import LessonOccurrenceParticipantRepository
from app.services import lesson_attendance_service
from app.services.auth.session_service import create_session

_participant_repo = LessonOccurrenceParticipantRepository()


# ============================== Helpers ==============================


async def _create_user(db, *, role: str | None = None, prefix: str = "tsk439") -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=f"{prefix}-user", tg_id=None)
    db.add(u)
    await db.flush()
    if role:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, id FROM roles WHERE name = :rn ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "rn": role},
        )
    await db.commit()
    return u.id


async def _create_occurrence_with_participant(
    db, *, student_id: int, teacher_id: int, scheduled_at: datetime,
    status: str = "scheduled", duration_minutes: int = 60,
) -> tuple[int, int]:
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


async def _create_course(db, *, title: str) -> int:
    res = await db.execute(
        text(
            "INSERT INTO courses (title, access_level, is_required, course_uid) "
            "VALUES (:t, 'self_guided', false, :uid) RETURNING id"
        ),
        {"t": title, "uid": f"tsk439-{random.randint(10**8, 10**10)}"},
    )
    cid = int(res.scalar_one())
    await db.commit()
    return cid


async def _create_material(db, *, course_id: int) -> int:
    res = await db.execute(
        text(
            "INSERT INTO materials (title, type, content, course_id, is_active) "
            "VALUES (:t, 'text', CAST(:c AS jsonb), :cid, true) RETURNING id"
        ),
        {
            "t": f"tsk439-mat-{random.randint(10**8, 10**10)}",
            "c": json.dumps({"text": "test"}),
            "cid": course_id,
        },
    )
    mid = res.scalar_one()
    await db.commit()
    return mid


NOW = datetime(2026, 8, 3, 10, 30, tzinfo=timezone.utc)  # понедельник, внутри окна ниже


# ============================== Repo ==============================


@pytest.mark.asyncio
async def test_repo_finds_participant_within_window(db):
    student_id = await _create_user(db, role="student", prefix="tsk439-repo1")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk439-repo1t")
    scheduled_at = NOW - timedelta(minutes=15)  # началось 15 мин назад, длительность 60 мин
    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id, scheduled_at=scheduled_at,
    )
    found = await _participant_repo.get_current_scheduled_for_student(
        db, student_id=student_id, now=NOW,
    )
    assert found is not None
    assert found.status == "scheduled"


@pytest.mark.asyncio
async def test_repo_none_before_window_starts(db):
    student_id = await _create_user(db, role="student", prefix="tsk439-repo2")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk439-repo2t")
    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id, scheduled_at=NOW + timedelta(minutes=10),
    )
    found = await _participant_repo.get_current_scheduled_for_student(
        db, student_id=student_id, now=NOW,
    )
    assert found is None


@pytest.mark.asyncio
async def test_repo_none_after_window_ends(db):
    student_id = await _create_user(db, role="student", prefix="tsk439-repo3")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk439-repo3t")
    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=NOW - timedelta(hours=2), duration_minutes=60,
    )
    found = await _participant_repo.get_current_scheduled_for_student(
        db, student_id=student_id, now=NOW,
    )
    assert found is None


@pytest.mark.asyncio
async def test_repo_ignores_non_scheduled_status(db):
    student_id = await _create_user(db, role="student", prefix="tsk439-repo4")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk439-repo4t")
    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=NOW - timedelta(minutes=15), status="declined",
    )
    found = await _participant_repo.get_current_scheduled_for_student(
        db, student_id=student_id, now=NOW,
    )
    assert found is None


# ============================== Service ==============================


@pytest.mark.asyncio
async def test_service_confirms_and_logs_auto_joined_event(db):
    student_id = await _create_user(db, role="student", prefix="tsk439-svc1")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk439-svc1t")
    occ_id, participant_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    result = await lesson_attendance_service.auto_confirm_if_in_progress(db, student_id=student_id)
    assert result is True

    row = (
        await db.execute(
            text("SELECT status FROM lesson_occurrence_participant WHERE id = :pid"),
            {"pid": participant_id},
        )
    ).fetchone()
    assert row[0] == "confirmed"

    event = (
        await db.execute(
            text("SELECT action, actor_user_id FROM attendance_event WHERE occurrence_id = :oid"),
            {"oid": occ_id},
        )
    ).fetchone()
    assert event is not None
    assert event[0] == "auto_joined"
    assert event[1] == student_id


@pytest.mark.asyncio
async def test_service_noop_when_no_active_occurrence(db):
    student_id = await _create_user(db, role="student", prefix="tsk439-svc2")
    result = await lesson_attendance_service.auto_confirm_if_in_progress(db, student_id=student_id)
    assert result is False


# ============================== Integration: material complete ==============================


@pytest.mark.asyncio
async def test_material_complete_auto_confirms_active_lesson(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk439-int1")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk439-int1t")
    token, _, _ = await create_session(db, user_id=student_id)
    course_id = await _create_course(db, title="tsk439 course")
    material_id = await _create_material(db, course_id=course_id)
    occ_id, participant_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )

    resp = await client.post(
        f"/api/v1/learning/materials/{material_id}/complete",
        json={"student_id": student_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    row = (
        await db.execute(
            text("SELECT status FROM lesson_occurrence_participant WHERE id = :pid"),
            {"pid": participant_id},
        )
    ).fetchone()
    assert row[0] == "confirmed"


@pytest.mark.asyncio
async def test_material_complete_does_not_override_declined(db, client):
    """Ученик явно отказался — реальное действие не должно тихо переписать
    его выбор обратно на confirmed (репозиторный фильтр status='scheduled')."""
    student_id = await _create_user(db, role="student", prefix="tsk439-int2")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk439-int2t")
    token, _, _ = await create_session(db, user_id=student_id)
    course_id = await _create_course(db, title="tsk439 course 2")
    material_id = await _create_material(db, course_id=course_id)
    occ_id, participant_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        status="declined",
    )

    resp = await client.post(
        f"/api/v1/learning/materials/{material_id}/complete",
        json={"student_id": student_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    row = (
        await db.execute(
            text("SELECT status FROM lesson_occurrence_participant WHERE id = :pid"),
            {"pid": participant_id},
        )
    ).fetchone()
    assert row[0] == "declined"


@pytest.mark.asyncio
async def test_material_complete_noop_without_active_lesson(db, client):
    student_id = await _create_user(db, role="student", prefix="tsk439-int3")
    token, _, _ = await create_session(db, user_id=student_id)
    course_id = await _create_course(db, title="tsk439 course 3")
    material_id = await _create_material(db, course_id=course_id)

    resp = await client.post(
        f"/api/v1/learning/materials/{material_id}/complete",
        json={"student_id": student_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
