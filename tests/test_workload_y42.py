"""Integration тест Y-4.2: get_teacher_workload pending_manual_reviews_total
исключает автопроверенные задачи.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session
from app.services.teacher_queue_service import get_teacher_workload


async def _setup_methodist(db) -> int:
    u = Users(
        email=f"y42wl-mth-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="y42wl-methodist", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    await create_session(db, user_id=u.id)
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, id FROM roles WHERE name='methodist' "
            "ON CONFLICT DO NOTHING"
        ),
        {"u": u.id},
    )
    await db.commit()
    return u.id


async def _create_task(db, *, course_id: int, type_: str) -> int:
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, course_id, difficulty_id) "
            "VALUES (:ext, 10, CAST(:content AS jsonb), :cid, 1) RETURNING id"
        ),
        {
            "ext": f"y42wl-test-{random.randint(10**8, 10**10)}",
            "content": json.dumps({"type": type_, "stem": "test"}),
            "cid": course_id,
        },
    )
    tid = res.scalar_one()
    await db.commit()
    return tid


async def _create_tr(db, *, user_id: int, task_id: int, is_correct: bool | None) -> int:
    now = datetime.now(timezone.utc)
    res = await db.execute(
        text(
            "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, "
            "received_at, max_score, source_system, is_correct) "
            "VALUES (0, :u, :t, :now, 0, :now, 10, 'spw', :ic) RETURNING id"
        ),
        {"u": user_id, "t": task_id, "now": now, "ic": is_correct},
    )
    rid = res.scalar_one()
    await db.commit()
    return rid


async def _create_student(db) -> int:
    u = Users(
        email=f"y42wl-stud-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="y42wl-stud", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


@pytest.mark.asyncio
async def test_pending_manual_reviews_total_excludes_auto_checked(db):
    """workload.pending_manual_reviews_total = X+1 после INSERT 1 SA_COM pending,
    несмотря на параллельный INSERT 1 MC (auto-checked).
    """
    methodist_id = await _setup_methodist(db)
    student_id = await _create_student(db)
    sa_com_task = await _create_task(db, course_id=1, type_="SA_COM")
    mc_task = await _create_task(db, course_id=1, type_="MC")
    try:
        before = await get_teacher_workload(db, teacher_id=methodist_id)
        baseline = before["pending_manual_reviews_total"]

        # tsk-210: под Y-6 первично-верный pending SA_COM = is_correct=TRUE.
        sa = await _create_tr(
            db, user_id=student_id, task_id=sa_com_task, is_correct=True
        )
        mc = await _create_tr(
            db, user_id=student_id, task_id=mc_task, is_correct=False
        )
        try:
            after = await get_teacher_workload(db, teacher_id=methodist_id)
            assert after["pending_manual_reviews_total"] == baseline + 1, (
                f"SA_COM pending добавляет +1, MC auto-checked НЕ добавляет: "
                f"baseline={baseline}, after={after['pending_manual_reviews_total']}"
            )
        finally:
            await db.execute(
                text("DELETE FROM task_results WHERE id = ANY(:r)"),
                {"r": [sa, mc]},
            )
            await db.commit()
    finally:
        await db.execute(
            text("DELETE FROM tasks WHERE id = ANY(:t)"),
            {"t": [sa_com_task, mc_task]},
        )
        await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": methodist_id})
        await db.execute(
            text("DELETE FROM user_session WHERE user_id IN (:m,:s)"),
            {"m": methodist_id, "s": student_id},
        )
        await db.execute(
            text("DELETE FROM identity_link WHERE user_id IN (:m,:s)"),
            {"m": methodist_id, "s": student_id},
        )
        await db.commit()
