"""Integration HTTP-тесты Y-4.2: pending-count исключает auto-checked."""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _setup_methodist(db) -> tuple[int, str]:
    u = Users(
        email=f"y42pc-mth-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="y42pc-methodist", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, id FROM roles WHERE name='methodist' "
            "ON CONFLICT DO NOTHING"
        ),
        {"u": u.id},
    )
    await db.commit()
    return u.id, token


async def _create_student(db) -> int:
    u = Users(
        email=f"y42pc-stud-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="y42pc-stud", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _create_task(db, *, course_id: int, type_: str) -> int:
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, course_id, difficulty_id) "
            "VALUES (:ext, 10, CAST(:content AS jsonb), :cid, 1) RETURNING id"
        ),
        {
            "ext": f"y42pc-test-{random.randint(10**8, 10**10)}",
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


async def _cleanup(db, *, methodist_id: int, student_id: int, task_ids: list[int], rids: list[int]):
    if rids:
        await db.execute(text("DELETE FROM task_results WHERE id = ANY(:r)"), {"r": rids})
    if task_ids:
        await db.execute(text("DELETE FROM tasks WHERE id = ANY(:t)"), {"t": task_ids})
    await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": methodist_id})
    await db.execute(text("DELETE FROM user_session WHERE user_id IN (:m,:s)"), {"m": methodist_id, "s": student_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id IN (:m,:s)"), {"m": methodist_id, "s": student_id})
    await db.commit()


@pytest.mark.asyncio
async def test_pending_count_excludes_auto_checked_mc(db, client):
    """MC c is_correct=False НЕ должен попадать в pending-count."""
    methodist_id, token = await _setup_methodist(db)
    student_id = await _create_student(db)
    task_id = await _create_task(db, course_id=1, type_="MC")
    rid = await _create_tr(db, user_id=student_id, task_id=task_id, is_correct=False)
    try:
        # Baseline (без нашей вставки count может быть >= 0). Сравниваем relative:
        # после удаления нашей записи — count не изменится (мы её не добавили в счётчик).
        # Просто проверяем что count не зависит от нашей MC-записи.
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending-count?teacher_id={methodist_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        with_mc = resp.json()["count"]
        # Удаляем MC записи, проверяем что count не уменьшился
        await db.execute(text("DELETE FROM task_results WHERE id=:r"), {"r": rid})
        await db.commit()
        resp2 = await client.get(
            f"/api/v1/teacher/reviews/pending-count?teacher_id={methodist_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        without_mc = resp2.json()["count"]
        assert with_mc == without_mc, (
            f"Auto-checked MC не должен учитываться в pending-count: "
            f"with={with_mc}, without={without_mc}"
        )
    finally:
        await _cleanup(
            db, methodist_id=methodist_id, student_id=student_id,
            task_ids=[task_id], rids=[],
        )


@pytest.mark.asyncio
async def test_pending_count_includes_pending_sa_com(db, client):
    """SA_COM с is_correct=NULL увеличивает pending-count на 1."""
    methodist_id, token = await _setup_methodist(db)
    student_id = await _create_student(db)
    task_id = await _create_task(db, course_id=1, type_="SA_COM")
    try:
        resp_before = await client.get(
            f"/api/v1/teacher/reviews/pending-count?teacher_id={methodist_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        before = resp_before.json()["count"]
        rid = await _create_tr(db, user_id=student_id, task_id=task_id, is_correct=None)
        try:
            resp_after = await client.get(
                f"/api/v1/teacher/reviews/pending-count?teacher_id={methodist_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            after = resp_after.json()["count"]
            assert after == before + 1, (
                f"pending SA_COM должен увеличить count: before={before}, after={after}"
            )
        finally:
            await db.execute(text("DELETE FROM task_results WHERE id=:r"), {"r": rid})
            await db.commit()
    finally:
        await _cleanup(
            db, methodist_id=methodist_id, student_id=student_id,
            task_ids=[task_id], rids=[],
        )


@pytest.mark.asyncio
async def test_pending_count_with_mixed_types(db, client):
    """Mixed: 1 auto-checked MC + 2 pending SA_COM → count увеличен на 2 (MC игнорируется)."""
    methodist_id, token = await _setup_methodist(db)
    student_id = await _create_student(db)
    mc_task = await _create_task(db, course_id=1, type_="MC")
    sa_com_task = await _create_task(db, course_id=1, type_="SA_COM")
    try:
        resp_before = await client.get(
            f"/api/v1/teacher/reviews/pending-count?teacher_id={methodist_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        before = resp_before.json()["count"]

        mc_rid = await _create_tr(
            db, user_id=student_id, task_id=mc_task, is_correct=False
        )
        sa1 = await _create_tr(
            db, user_id=student_id, task_id=sa_com_task, is_correct=None
        )
        sa2 = await _create_tr(
            db, user_id=student_id, task_id=sa_com_task, is_correct=None
        )
        try:
            resp_after = await client.get(
                f"/api/v1/teacher/reviews/pending-count?teacher_id={methodist_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            after = resp_after.json()["count"]
            assert after == before + 2, (
                f"2 SA_COM pending + 1 MC auto-checked → +2 (не +3): "
                f"before={before}, after={after}"
            )
        finally:
            await db.execute(
                text("DELETE FROM task_results WHERE id = ANY(:r)"),
                {"r": [mc_rid, sa1, sa2]},
            )
            await db.commit()
    finally:
        await _cleanup(
            db, methodist_id=methodist_id, student_id=student_id,
            task_ids=[mc_task, sa_com_task], rids=[],
        )
