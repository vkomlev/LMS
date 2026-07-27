"""tsk-022/tsk-410 (Календарь LMS): сводка преподавателя по occurrence — общий
эндпоинт для карточки "до занятия" и кнопки "Подвести итоги" после.

Проверяем на НАСТОЯЩЕЙ БД (не на моках), по образцу test_manual_progress_tsk297.py/
test_activity_feed_tsk408.py.

Покрывает:
- `GET /teacher/lesson-occurrences/{id}/summary`: ownership 403/404, профиль
  ученика (full_name/tg_id), ad-hoc флаг occurrence.
- Метрики ДЗ за окно "между занятиями": выполнено/с первого раза (по факту
  отсутствия более раннего результата, НЕ по count_retry — он всегда 0 в
  реальном потоке сдачи ответа), запросил помощь.
- Заблокированные лимитом попыток задания + % прогресса курса (снепшот,
  переиспользует get_student_progress).
- Открытая заявка помощи — с текстом, не только счётчик.
- Серия пропусков подряд (missed_streak).
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
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

UTC = timezone.utc
_TAG = "tsk022"


# ============================== Helpers ==============================


async def _new_user(db, *, role: str | None, name: str) -> tuple[int, str]:
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    await db.commit()
    return u.id, token


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


async def _new_course(db, title: str) -> int:
    return (
        await db.execute(
            text("INSERT INTO courses (title, access_level) VALUES (:t, 'self_guided') RETURNING id"),
            {"t": title},
        )
    ).scalar()


async def _enroll_student(db, *, student_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": student_id, "c": course_id},
    )
    await db.commit()


async def _link_student_teacher(db, *, student_id: int, teacher_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO student_teacher_links (student_id, teacher_id) "
            "VALUES (:s, :t) ON CONFLICT DO NOTHING"
        ),
        {"s": student_id, "t": teacher_id},
    )
    await db.commit()


async def _new_task(db, *, course_id: int, uid: str) -> int:
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    assert difficulty_id is not None, "нет difficulties — граф не собрать"
    content = {"type": "SA", "stem": f"{_TAG} условие {uid}"}
    return (
        await db.execute(
            text(
                "INSERT INTO tasks (task_content, solution_rules, course_id, "
                "difficulty_id, external_uid, max_score, order_position) "
                "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, 10, 1) "
                "RETURNING id"
            ),
            {
                "tc": json.dumps(content),
                "sr": json.dumps({"max_score": 10}),
                "cid": course_id,
                "did": difficulty_id,
                "uid": f"{_TAG}-{uid}-{random.randint(10**8, 10**10)}",
            },
        )
    ).scalar()


async def _insert_task_result(
    db, *, student_id: int, task_id: int, course_id: int, is_correct: bool, submitted_at: datetime,
) -> None:
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :c, 'test') RETURNING id"
            ),
            {"u": student_id, "c": course_id},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, checked_at, source_system) "
            "VALUES (:u, :t, :a, :sc, 10, :ok, :ts, :ts, 0, :ts, 'test')"
        ),
        {
            "u": student_id, "t": task_id, "a": attempt_id,
            "sc": 10 if is_correct else 0, "ok": is_correct, "ts": submitted_at,
        },
    )
    await db.commit()


async def _insert_help_request(
    db, *, student_id: int, task_id: int, course_id: int, teacher_id: int, created_at: datetime,
) -> None:
    await db.execute(
        text(
            "INSERT INTO help_requests "
            "(status, request_type, student_id, task_id, course_id, assigned_teacher_id, "
            " message, created_at, updated_at) "
            "VALUES ('open', 'manual_help', :s, :t, :c, :teach, :msg, :ts, :ts)"
        ),
        {
            "s": student_id, "t": task_id, "c": course_id, "teach": teacher_id,
            "msg": f"{_TAG} нужна помощь", "ts": created_at,
        },
    )
    await db.commit()


# ============================== Ownership ==============================


@pytest.mark.asyncio
async def test_summary_403_for_other_teacher(db, client):
    teacher_a, _ = await _new_user(db, role="teacher", name="teachA")
    teacher_b, token_b = await _new_user(db, role="teacher", name="teachB")
    student_id, _ = await _new_user(db, role="student", name="stud")

    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_a,
        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
    )

    resp = await client.get(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/summary",
        params={"teacher_id": teacher_a},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_summary_403_when_own_teacher_id_but_foreign_occurrence(db, client):
    """IDOR: атакующий передаёт СВОЙ teacher_id (проходит `_ensure_self_or_service`),
    но occurrence принадлежит ДРУГОМУ преподавателю — второй уровень гейта
    (`get_occurrence_for_teacher`) обязан отклонить независимо от первого."""
    teacher_a, _ = await _new_user(db, role="teacher", name="teachA")
    teacher_b, token_b = await _new_user(db, role="teacher", name="teachB")
    student_id, _ = await _new_user(db, role="student", name="stud")

    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_a,
        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
    )

    resp = await client.get(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/summary",
        params={"teacher_id": teacher_b},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_summary_404_for_unknown_occurrence(db, client):
    teacher_id, token = await _new_user(db, role="teacher", name="teach")

    resp = await client.get(
        "/api/v1/teacher/lesson-occurrences/999999999/summary",
        params={"teacher_id": teacher_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, resp.text


# ============================== Basic shape ==============================


@pytest.mark.asyncio
async def test_summary_basic_shape_and_ad_hoc_flag(db, client):
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")

    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
    )

    resp = await client.get(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/summary",
        params={"teacher_id": teacher_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["occurrence_id"] == occ_id
    assert body["is_ad_hoc"] is True
    assert len(body["participants"]) == 1
    p = body["participants"][0]
    assert p["student_id"] == student_id
    assert p["status"] == "scheduled"
    assert p["missed_streak"] == 0
    assert p["window_from"] is None
    assert p["homework"] == {"completed": 0, "first_try": 0, "help_requested": 0}


# ============================== Homework window metrics ==============================


@pytest.mark.asyncio
async def test_summary_completed_and_first_try_from_prior_occurrence_window(db, client):
    """Задание, сданное верно ПОСЛЕ конца предыдущего occurrence и БЕЗ более
    раннего результата — считается выполненным и "с первого раза"."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    course_id = await _new_course(db, f"{_TAG}-course")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)
    task_id = await _new_task(db, course_id=course_id, uid="a")

    now = datetime.now(UTC)
    prev_occ_end = now - timedelta(days=6, hours=23)
    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=prev_occ_end - timedelta(hours=1),
        status="completed", duration_minutes=60,
    )
    current_occ_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now + timedelta(hours=1),
    )

    await _insert_task_result(
        db, student_id=student_id, task_id=task_id, course_id=course_id,
        is_correct=True, submitted_at=now - timedelta(hours=1),
    )

    resp = await client.get(
        f"/api/v1/teacher/lesson-occurrences/{current_occ_id}/summary",
        params={"teacher_id": teacher_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    p = resp.json()["participants"][0]
    assert p["window_from"] is not None
    assert p["homework"]["completed"] == 1
    assert p["homework"]["first_try"] == 1
    assert p["last_activity"]["kind"] == "task"
    assert p["days_since_last_activity"] == 0


@pytest.mark.asyncio
async def test_summary_not_first_try_when_earlier_result_exists(db, client):
    """Тот же student+task уже сдавался раньше (пусть и до окна) — успех в
    окне НЕ считается "с первого раза", хотя всё равно "выполнено"."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    course_id = await _new_course(db, f"{_TAG}-course")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)
    task_id = await _new_task(db, course_id=course_id, uid="a")

    now = datetime.now(UTC)
    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now + timedelta(hours=1),
    )

    await _insert_task_result(
        db, student_id=student_id, task_id=task_id, course_id=course_id,
        is_correct=False, submitted_at=now - timedelta(days=30),
    )
    await _insert_task_result(
        db, student_id=student_id, task_id=task_id, course_id=course_id,
        is_correct=True, submitted_at=now - timedelta(hours=1),
    )

    resp = await client.get(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/summary",
        params={"teacher_id": teacher_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    p = resp.json()["participants"][0]
    assert p["homework"]["completed"] == 1
    assert p["homework"]["first_try"] == 0


@pytest.mark.asyncio
async def test_summary_help_requested_counted_in_window(db, client):
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    course_id = await _new_course(db, f"{_TAG}-course")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)
    task_id = await _new_task(db, course_id=course_id, uid="a")

    now = datetime.now(UTC)
    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now + timedelta(hours=1),
    )
    await _insert_help_request(
        db, student_id=student_id, task_id=task_id, course_id=course_id,
        teacher_id=teacher_id, created_at=now - timedelta(hours=2),
    )

    resp = await client.get(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/summary",
        params={"teacher_id": teacher_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    p = resp.json()["participants"][0]
    assert p["homework"]["help_requested"] == 1
    assert len(p["open_help_requests"]) == 1
    assert p["open_help_requests"][0]["task_title"]


# ============================== Course progress + blocked ==============================


@pytest.mark.asyncio
async def test_summary_course_progress_percent(db, client):
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    course_id = await _new_course(db, f"{_TAG}-course")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)
    await _enroll_student(db, student_id=student_id, course_id=course_id)
    task_a = await _new_task(db, course_id=course_id, uid="a")
    await _new_task(db, course_id=course_id, uid="b")

    now = datetime.now(UTC)
    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now + timedelta(hours=1),
    )

    await _insert_task_result(
        db, student_id=student_id, task_id=task_a, course_id=course_id,
        is_correct=True, submitted_at=now - timedelta(hours=1),
    )

    resp = await client.get(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/summary",
        params={"teacher_id": teacher_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    p = resp.json()["participants"][0]
    progress = next((c for c in p["course_progress"] if c["course_id"] == course_id), None)
    assert progress is not None
    assert progress["percent_complete"] == 50


# ============================== Missed streak ==============================


@pytest.mark.asyncio
async def test_summary_missed_streak_counts_consecutive_no_show(db, client):
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")

    now = datetime.now(UTC)
    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=21), status="no_show",
    )
    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=14), status="confirmed",
    )
    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=7), status="no_show",
    )
    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now + timedelta(hours=1),
    )

    resp = await client.get(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/summary",
        params={"teacher_id": teacher_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    p = resp.json()["participants"][0]
    assert p["missed_streak"] == 1


@pytest.mark.asyncio
async def test_summary_group_occurrence_all_participants_present(db, client):
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_a, _ = await _new_user(db, role="student", name="stuA")
    student_b, _ = await _new_user(db, role="student", name="stuB")

    occ_id = await _create_occurrence_with_participant(
        db, student_id=student_a, teacher_id=teacher_id,
        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add(LessonOccurrenceParticipant(occurrence_id=occ_id, student_id=student_b, status="scheduled"))
    await db.commit()

    resp = await client.get(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/summary",
        params={"teacher_id": teacher_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    ids = {p["student_id"] for p in resp.json()["participants"]}
    assert ids == {student_a, student_b}
