"""Integration HTTP-тесты POST /api/v1/teacher/reviews/{id}/grade (Phase Y-4).

Покрывает:
- 401 без auth
- 403 чужой teacher_id
- 404 task_result не существует
- happy path: grade сохраняется, inbox создан, audit_event записан
- 409 при повторном grade (уже оценено)
- 409 при mismatch lock_token
- 422 при score > max_score
"""
from __future__ import annotations

import random
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _setup_teacher_with_session(db, *, course_id: int | None = None):
    """Создать teacher + session + опц. teacher_courses привязку."""
    teacher = Users(
        email=f"y4-teacher-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="Y4-teacher", tg_id=None,
    )
    db.add(teacher)
    await db.flush()
    await identity_link_service.upsert_identity(db, teacher.id, "email", teacher.email)
    access_token, _, _ = await create_session(db, user_id=teacher.id)
    if course_id is not None:
        await db.execute(
            text(
                "INSERT INTO teacher_courses (teacher_id, course_id, linked_at) "
                "VALUES (:t, :c, now()) ON CONFLICT DO NOTHING"
            ),
            {"t": teacher.id, "c": course_id},
        )
    await db.commit()
    return teacher.id, access_token


async def _setup_student_with_email(db) -> tuple[int, str]:
    email = f"y4-student-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name="Y4-student", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.commit()
    return u.id, email


async def _pick_or_create_task(db) -> tuple[int, int]:
    """Найти задачу в курсе без parents (DB триггер запрещает линковку teacher
    к курсу с parents). Возвращает (task_id, course_id)."""
    row = (
        await db.execute(
            text(
                "SELECT id, course_id FROM tasks "
                "WHERE course_id IS NOT NULL "
                "  AND course_id NOT IN (SELECT course_id FROM course_parents) "
                "LIMIT 1"
            )
        )
    ).fetchone()
    if row is None:
        pytest.skip("Нет задач в root-курсе — пропускаем тест grade")
    return int(row[0]), int(row[1])


async def _create_pending_task_result(
    db, *, student_id: int, task_id: int, teacher_id: int,
    max_score: int = 10, lock_token: str | None = None,
) -> tuple[int, str, datetime]:
    """Создать task_result с захватом этим teacher'ом."""
    if lock_token is None:
        lock_token = secrets.token_hex(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=5)
    res = await db.execute(
        text(
            "INSERT INTO task_results "
            "(score, user_id, task_id, submitted_at, count_retry, received_at, "
            " max_score, source_system, review_claimed_by, review_claim_token, review_claim_expires_at) "
            "VALUES (0, :u, :t, :now, 0, :now, :ms, 'spw', :tid, :tok, :exp) "
            "RETURNING id"
        ),
        {
            "u": student_id, "t": task_id, "now": now,
            "ms": max_score, "tid": teacher_id, "tok": lock_token, "exp": expires_at,
        },
    )
    rid = res.scalar_one()
    await db.commit()
    return rid, lock_token, expires_at


async def _cleanup_grade(db, *, result_id: int, student_id: int, teacher_id: int):
    await db.execute(
        text("DELETE FROM notifications WHERE user_id IN (:s)"), {"s": student_id}
    )
    await db.execute(text("DELETE FROM task_results WHERE id=:r"), {"r": result_id})
    await db.execute(
        text("DELETE FROM teacher_courses WHERE teacher_id=:t"), {"t": teacher_id}
    )
    await db.execute(
        text("DELETE FROM user_session WHERE user_id IN (:t, :s)"),
        {"t": teacher_id, "s": student_id},
    )
    await db.execute(
        text("DELETE FROM identity_link WHERE user_id IN (:t, :s)"),
        {"t": teacher_id, "s": student_id},
    )
    await db.commit()


@pytest.mark.asyncio
async def test_grade_requires_auth(client):
    resp = await client.post(
        "/api/v1/teacher/reviews/1/grade",
        json={"teacher_id": 1, "lock_token": "x", "score": 5, "is_correct": True},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_grade_wrong_teacher_id_403(db, client):
    teacher_id, token = await _setup_teacher_with_session(db)
    try:
        resp = await client.post(
            "/api/v1/teacher/reviews/1/grade",
            json={
                "teacher_id": teacher_id + 9999,  # чужой
                "lock_token": "x", "score": 5, "is_correct": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
    finally:
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": teacher_id})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": teacher_id})
        await db.commit()


@pytest.mark.asyncio
async def test_grade_not_found_404(db, client):
    teacher_id, token = await _setup_teacher_with_session(db)
    try:
        resp = await client.post(
            "/api/v1/teacher/reviews/9999999/grade",
            json={
                "teacher_id": teacher_id, "lock_token": "x",
                "score": 5, "is_correct": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
    finally:
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": teacher_id})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": teacher_id})
        await db.commit()


@pytest.mark.asyncio
async def test_grade_happy_path_creates_inbox_and_audit(db, client):
    task_id, course_id = await _pick_or_create_task(db)
    teacher_id, token = await _setup_teacher_with_session(db, course_id=course_id)
    student_id, _ = await _setup_student_with_email(db)
    rid, lock_token, _ = await _create_pending_task_result(
        db, student_id=student_id, task_id=task_id, teacher_id=teacher_id, max_score=10,
    )
    try:
        resp = await client.post(
            f"/api/v1/teacher/reviews/{rid}/grade",
            json={
                "teacher_id": teacher_id, "lock_token": lock_token,
                "score": 8, "is_correct": True, "comment": "Хорошо",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["result_id"] == rid
        assert body["score"] == 8
        assert body["is_correct"] is True
        assert body["comment"] == "Хорошо"
        assert body["notification_id"] > 0

        # Проверка БД: inbox запись создана
        notif_count = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM notifications "
                    "WHERE user_id=:s AND kind='sa_com_graded'"
                ),
                {"s": student_id},
            )
        ).scalar()
        assert notif_count == 1

        # task_results обновлён
        tr_row = (
            await db.execute(
                text(
                    "SELECT is_correct, score, checked_at, checked_by, "
                    "review_claimed_by, review_claim_token "
                    "FROM task_results WHERE id=:r"
                ),
                {"r": rid},
            )
        ).fetchone()
        assert tr_row[0] is True
        assert tr_row[1] == 8
        assert tr_row[2] is not None
        assert tr_row[3] == teacher_id
        assert tr_row[4] is None  # claim cleared
        assert tr_row[5] is None

        # Audit event записан
        audit_count = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM audit_event "
                    "WHERE event_type='teacher.review.graded' AND user_id=:t"
                ),
                {"t": teacher_id},
            )
        ).scalar()
        assert audit_count >= 1
    finally:
        await _cleanup_grade(db, result_id=rid, student_id=student_id, teacher_id=teacher_id)


@pytest.mark.asyncio
async def test_grade_idempotent_second_call_returns_409(db, client):
    """Повторный grade с тем же lock_token → 409 «уже оценено»."""
    task_id, course_id = await _pick_or_create_task(db)
    teacher_id, token = await _setup_teacher_with_session(db, course_id=course_id)
    student_id, _ = await _setup_student_with_email(db)
    rid, lock_token, _ = await _create_pending_task_result(
        db, student_id=student_id, task_id=task_id, teacher_id=teacher_id, max_score=10,
    )
    try:
        resp1 = await client.post(
            f"/api/v1/teacher/reviews/{rid}/grade",
            json={
                "teacher_id": teacher_id, "lock_token": lock_token,
                "score": 7, "is_correct": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp1.status_code == 200

        resp2 = await client.post(
            f"/api/v1/teacher/reviews/{rid}/grade",
            json={
                "teacher_id": teacher_id, "lock_token": lock_token,
                "score": 9, "is_correct": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 409
    finally:
        await _cleanup_grade(db, result_id=rid, student_id=student_id, teacher_id=teacher_id)


@pytest.mark.asyncio
async def test_grade_score_exceeds_max_returns_422(db, client):
    task_id, course_id = await _pick_or_create_task(db)
    teacher_id, token = await _setup_teacher_with_session(db, course_id=course_id)
    student_id, _ = await _setup_student_with_email(db)
    rid, lock_token, _ = await _create_pending_task_result(
        db, student_id=student_id, task_id=task_id, teacher_id=teacher_id, max_score=10,
    )
    try:
        resp = await client.post(
            f"/api/v1/teacher/reviews/{rid}/grade",
            json={
                "teacher_id": teacher_id, "lock_token": lock_token,
                "score": 99, "is_correct": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422
    finally:
        await _cleanup_grade(db, result_id=rid, student_id=student_id, teacher_id=teacher_id)


@pytest.mark.asyncio
async def test_grade_wrong_lock_token_returns_409(db, client):
    task_id, course_id = await _pick_or_create_task(db)
    teacher_id, token = await _setup_teacher_with_session(db, course_id=course_id)
    student_id, _ = await _setup_student_with_email(db)
    rid, _real_lock, _ = await _create_pending_task_result(
        db, student_id=student_id, task_id=task_id, teacher_id=teacher_id, max_score=10,
    )
    try:
        resp = await client.post(
            f"/api/v1/teacher/reviews/{rid}/grade",
            json={
                "teacher_id": teacher_id, "lock_token": "wrong-token",
                "score": 5, "is_correct": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 409
    finally:
        await _cleanup_grade(db, result_id=rid, student_id=student_id, teacher_id=teacher_id)
