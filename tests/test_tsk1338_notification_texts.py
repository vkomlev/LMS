"""tsk-1338: тексты уведомлений с именами вместо номеров + отметка «обработано».

Покрывает:
- `course_pending_review`: имя ученика и название курса в тексте, без голых id;
  `student_name`/`course_title` в payload.
- `lesson_missed` преподавателю: имя ученика в тексте и payload.
- `user_registered`: уведомление маркетологу при регистрации.
- `POST /methodist/escalations/{id}/read`: своя запись — 200, чужая — 404.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from app.models.users import Users
from app.services import inbox_service, methodist_notify_service
from app.services.auth.session_service import create_session
from app.services.lesson_attendance_cron_service import lesson_attendance_cron_tick
from app.services.registration_notify_service import notify_marketers_new_user


async def _create_user(db, *, role: str | None = None, full_name: str | None = None) -> int:
    """Пользователь (+ роль), сразу закоммичен."""
    email = f"tsk1338-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=full_name, tg_id=None)
    db.add(u)
    await db.flush()
    if role:
        role_id = (
            await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role})
        ).scalar()
        if role_id is None:
            pytest.skip(f"В тестовой БД нет роли {role}")
        await db.execute(
            text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r) ON CONFLICT DO NOTHING"),
            {"u": u.id, "r": role_id},
        )
    await db.commit()
    return int(u.id)


@pytest.mark.asyncio
async def test_course_pending_review_has_names_not_ids(db):
    methodist_id = await _create_user(db, role="methodist", full_name="Методист Тест")
    student_id = await _create_user(db, role="student", full_name="Анна Ученица")
    course_title = f"Курс tsk1338 {random.randint(10**6, 10**7)}"
    course_id = (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level, is_required) "
                "VALUES (:t, 'self_guided', false) RETURNING id"
            ),
            {"t": course_title},
        )
    ).scalar_one()
    await db.commit()
    try:
        created = await methodist_notify_service.escalate_course_completion(
            db,
            student_id=student_id,
            course_id=course_id,
            pending_result_ids=[-1, -2],
            rate_limit_per_day=1000,
        )
        await db.commit()
        assert created >= 1
        row = (
            await db.execute(
                text(
                    "SELECT content, payload FROM notifications "
                    "WHERE user_id = :u AND kind = 'course_pending_review'"
                ),
                {"u": methodist_id},
            )
        ).one()
        content, payload = row
        assert "Анна Ученица" in content
        assert f"«{course_title}»" in content
        assert str(student_id) not in content
        assert f" {course_id} " not in f" {content} "
        assert payload["student_name"] == "Анна Ученица"
        assert payload["course_title"] == course_title
        assert payload["student_id"] == student_id
    finally:
        await db.execute(
            text(
                "DELETE FROM notifications WHERE kind = 'course_pending_review' "
                "AND (payload->>'course_id')::int = :c"
            ),
            {"c": course_id},
        )
        await db.execute(text("DELETE FROM courses WHERE id = :c"), {"c": course_id})
        await db.commit()


@pytest.mark.asyncio
async def test_teacher_lesson_missed_has_student_name(db, db_session_factory):
    student_id = await _create_user(db, role="student", full_name="Пётр Пропустил")
    teacher_id = await _create_user(db, role="teacher", full_name="Учитель Тест")
    occ = LessonOccurrence(
        slot_id=None, teacher_id=teacher_id,
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        duration_minutes=60,
    )
    db.add(occ)
    await db.flush()
    db.add(LessonOccurrenceParticipant(occurrence_id=occ.id, student_id=student_id, status="scheduled"))
    await db.flush()
    occ_id = occ.id
    await db.commit()

    await lesson_attendance_cron_tick(db_session_factory)

    row = (
        await db.execute(
            text(
                "SELECT content, payload FROM notifications "
                "WHERE kind = 'lesson_missed' AND user_id = :t "
                "AND (payload->>'occurrence_id')::int = :oid"
            ),
            {"t": teacher_id, "oid": occ_id},
        )
    ).one()
    content, payload = row
    assert content.startswith("Пётр Пропустил не подтвердил(а) явку")
    assert payload["student_name"] == "Пётр Пропустил"
    assert payload["occurrence_id"] == occ_id


@pytest.mark.asyncio
async def test_user_registered_goes_to_marketer(db):
    marketer_id = await _create_user(db, role="marketer", full_name="Маркетолог Тест")
    new_user_id = await _create_user(db, full_name=None)  # без имени → email
    email = (
        await db.execute(text("SELECT email FROM users WHERE id = :u"), {"u": new_user_id})
    ).scalar_one()

    created = await notify_marketers_new_user(db, new_user_id, "magic_link")
    await db.commit()
    assert created >= 1
    row = (
        await db.execute(
            text(
                "SELECT title, content, payload FROM notifications "
                "WHERE user_id = :m AND kind = 'user_registered' "
                "AND (payload->>'student_id')::int = :s"
            ),
            {"m": marketer_id, "s": new_user_id},
        )
    ).one()
    title, content, payload = row
    assert title == "Новая регистрация"
    assert content == f"{email} зарегистрировался(-ась) через ссылку на почту."
    assert payload == {"student_id": new_user_id, "student_name": email, "via": "magic_link"}


@pytest.mark.asyncio
async def test_escalation_read_endpoint_own_and_foreign(db, client):
    meth_a = await _create_user(db, role="methodist", full_name="Методист А")
    meth_b = await _create_user(db, role="methodist", full_name="Методист Б")
    token_a, _, _ = await create_session(db, user_id=meth_a)
    own = await inbox_service.create_for_user(
        db, user_id=meth_a, kind="learning_gap_escalated", title="t", content="c",
        payload={"trigger": "tsk1338"}, created_by=None,
    )
    foreign = await inbox_service.create_for_user(
        db, user_id=meth_b, kind="learning_gap_escalated", title="t", content="c",
        payload={"trigger": "tsk1338"}, created_by=None,
    )
    await db.commit()
    own_id, foreign_id = int(own.id), int(foreign.id)
    headers = {"Authorization": f"Bearer {token_a}"}

    # learning_gap_escalated теперь виден в pending.
    resp = await client.get("/api/v1/methodist/escalations/pending", headers=headers)
    assert resp.status_code == 200, resp.text
    assert own_id in [i["id"] for i in resp.json()["items"]]

    resp = await client.post(f"/api/v1/methodist/escalations/{own_id}/read", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == own_id and body["read_at"]

    # Повтор — идемпотентно 200.
    resp = await client.post(f"/api/v1/methodist/escalations/{own_id}/read", headers=headers)
    assert resp.status_code == 200, resp.text

    resp = await client.post(f"/api/v1/methodist/escalations/{foreign_id}/read", headers=headers)
    assert resp.status_code == 404, resp.text
    read_at = (
        await db.execute(text("SELECT read_at FROM notifications WHERE id = :i"), {"i": foreign_id})
    ).scalar()
    assert read_at is None
