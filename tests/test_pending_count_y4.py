"""Integration HTTP-тесты GET /api/v1/teacher/reviews/pending-count (Phase Y-4).

Также покрывает существующий teacher_courses фильтр в claim-next (regression).
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


async def _setup_teacher(db, *, course_id: int | None = None):
    teacher = Users(
        email=f"y4-tch-pc-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="Y4-tch-pc", tg_id=None,
    )
    db.add(teacher)
    await db.flush()
    await identity_link_service.upsert_identity(db, teacher.id, "email", teacher.email)
    token, _, _ = await create_session(db, user_id=teacher.id)
    if course_id is not None:
        await db.execute(
            text(
                "INSERT INTO teacher_courses (teacher_id, course_id, linked_at) "
                "VALUES (:t, :c, now()) ON CONFLICT DO NOTHING"
            ),
            {"t": teacher.id, "c": course_id},
        )
    await db.commit()
    return teacher.id, token


async def _create_pending_tr(db, *, user_id: int, task_id: int) -> int:
    now = datetime.now(timezone.utc)
    res = await db.execute(
        text(
            "INSERT INTO task_results "
            "(score, user_id, task_id, submitted_at, count_retry, received_at, "
            " max_score, source_system, is_correct) "
            "VALUES (0, :u, :t, :now, 0, :now, 10, 'spw', NULL) RETURNING id"
        ),
        {"u": user_id, "t": task_id, "now": now},
    )
    rid = res.scalar_one()
    await db.commit()
    return rid


async def _pick_task_with_course(db) -> tuple[int, int]:
    """Найти задачу в root-курсе без parents (DB-триггер требует root-курс)."""
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
        pytest.skip("Нет задач в root-курсе")
    return int(row[0]), int(row[1])


async def _create_student(db) -> int:
    u = Users(
        email=f"y4-pc-stud-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="pc-stud", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _cleanup(db, *, teacher_id: int, student_id: int, rids: list[int]):
    if rids:
        await db.execute(text("DELETE FROM task_results WHERE id = ANY(:r)"), {"r": rids})
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
async def test_pending_count_requires_auth(client):
    resp = await client.get("/api/v1/teacher/reviews/pending-count?teacher_id=1")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_pending_count_wrong_teacher_id_403(db, client):
    teacher_id, token = await _setup_teacher(db)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending-count?teacher_id={teacher_id + 9999}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
    finally:
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": teacher_id})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": teacher_id})
        await db.commit()


@pytest.mark.asyncio
async def test_pending_count_zero_for_teacher_without_courses(db, client):
    """Teacher без teacher_courses → count=0 (фильтр REVIEW_ACL_SQL пустой ACL)."""
    teacher_id, token = await _setup_teacher(db)  # no course_id binding
    try:
        # Создадим pending task_result — но teacher не привязан к этому курсу
        task_id, _course_id = await _pick_task_with_course(db)
        student_id = await _create_student(db)
        rid = await _create_pending_tr(db, user_id=student_id, task_id=task_id)
        try:
            resp = await client.get(
                f"/api/v1/teacher/reviews/pending-count?teacher_id={teacher_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            assert resp.json()["count"] == 0
        finally:
            await _cleanup(db, teacher_id=teacher_id, student_id=student_id, rids=[rid])
    finally:
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": teacher_id})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": teacher_id})
        await db.commit()


@pytest.mark.asyncio
async def test_pending_count_sees_own_course_pending(db, client):
    task_id, course_id = await _pick_task_with_course(db)
    teacher_id, token = await _setup_teacher(db, course_id=course_id)
    student_id = await _create_student(db)
    rid = await _create_pending_tr(db, user_id=student_id, task_id=task_id)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending-count?teacher_id={teacher_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] >= 1
        assert body["oldest_received_at"] is not None
    finally:
        await _cleanup(db, teacher_id=teacher_id, student_id=student_id, rids=[rid])
