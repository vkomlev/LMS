"""Integration HTTP-тесты GET /me/history (Phase Y-4)."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _setup_user(db):
    email = f"y4-hist-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name="Y4-hist", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    access_token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    return u.id, access_token


async def _create_task_result(
    db, *, user_id: int, task_id: int,
    is_correct: bool | None, score: int = 0, max_score: int = 10,
    comment: str | None = None,
) -> int:
    """Создать task_result с заданным is_correct (None = pending_review)."""
    now = datetime.now(timezone.utc) - timedelta(seconds=random.randint(0, 1000))
    metrics = {"comment": comment} if comment else {}
    res = await db.execute(
        text(
            "INSERT INTO task_results "
            "(score, user_id, task_id, submitted_at, count_retry, received_at, "
            " max_score, source_system, is_correct, metrics, checked_at) "
            "VALUES (:s, :u, :t, :now, 0, :now, :ms, 'spw', :ic, "
            "        CAST(:metrics AS jsonb), :checked) "
            "RETURNING id"
        ),
        {
            "s": score, "u": user_id, "t": task_id, "now": now,
            "ms": max_score, "ic": is_correct,
            "metrics": __import__("json").dumps(metrics),
            "checked": now if is_correct is not None else None,
        },
    )
    rid = res.scalar_one()
    await db.commit()
    return rid


async def _pick_task(db) -> int:
    row = (await db.execute(text("SELECT id FROM tasks LIMIT 1"))).fetchone()
    if row is None:
        pytest.skip("Нет задач в БД")
    return int(row[0])


async def _cleanup(db, user_id: int, rids: list[int]):
    if rids:
        await db.execute(
            text("DELETE FROM task_results WHERE id = ANY(:r)"), {"r": rids}
        )
    await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
    await db.commit()


@pytest.mark.asyncio
async def test_history_requires_auth(client):
    resp = await client.get("/api/v1/me/history")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_history_returns_empty_for_new_user(db, client):
    uid, token = await _setup_user(db)
    try:
        resp = await client.get(
            "/api/v1/me/history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        await _cleanup(db, uid, [])


@pytest.mark.asyncio
async def test_history_filter_pending_passed_failed(db, client):
    uid, token = await _setup_user(db)
    task_id = await _pick_task(db)
    rids = []
    try:
        rids.append(await _create_task_result(
            db, user_id=uid, task_id=task_id, is_correct=None
        ))
        rids.append(await _create_task_result(
            db, user_id=uid, task_id=task_id, is_correct=True, score=10
        ))
        rids.append(await _create_task_result(
            db, user_id=uid, task_id=task_id, is_correct=False, score=0
        ))

        # all
        resp_all = await client.get(
            "/api/v1/me/history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_all.status_code == 200
        assert len(resp_all.json()) == 3

        # pending_review
        resp_pend = await client.get(
            "/api/v1/me/history?filter=pending_review",
            headers={"Authorization": f"Bearer {token}"},
        )
        items_pend = resp_pend.json()
        assert len(items_pend) == 1
        assert items_pend[0]["status"] == "pending_review"

        # passed
        resp_pass = await client.get(
            "/api/v1/me/history?filter=passed",
            headers={"Authorization": f"Bearer {token}"},
        )
        items_pass = resp_pass.json()
        assert len(items_pass) == 1
        assert items_pass[0]["status"] == "passed"

        # failed
        resp_fail = await client.get(
            "/api/v1/me/history?filter=failed",
            headers={"Authorization": f"Bearer {token}"},
        )
        items_fail = resp_fail.json()
        assert len(items_fail) == 1
        assert items_fail[0]["status"] == "failed"
    finally:
        await _cleanup(db, uid, rids)


@pytest.mark.asyncio
async def test_history_pagination(db, client):
    uid, token = await _setup_user(db)
    task_id = await _pick_task(db)
    rids = []
    try:
        for _ in range(5):
            rids.append(await _create_task_result(
                db, user_id=uid, task_id=task_id, is_correct=True, score=10
            ))

        resp = await client.get(
            "/api/v1/me/history?limit=2&offset=0",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 2
    finally:
        await _cleanup(db, uid, rids)


@pytest.mark.asyncio
async def test_history_returns_only_own_records(db, client):
    """IDOR: history фильтруется по current_user.id, чужие попытки не видны."""
    uid_a, token_a = await _setup_user(db)
    uid_b, _ = await _setup_user(db)
    task_id = await _pick_task(db)
    rids = []
    try:
        rids.append(await _create_task_result(
            db, user_id=uid_a, task_id=task_id, is_correct=True, score=10
        ))
        rids.append(await _create_task_result(
            db, user_id=uid_b, task_id=task_id, is_correct=True, score=10
        ))

        resp = await client.get(
            "/api/v1/me/history",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        items = resp.json()
        # Все записи должны быть только от uid_a — uid_a имел только 1 попытку
        assert len(items) == 1
    finally:
        await _cleanup(db, uid_a, [])
        await _cleanup(db, uid_b, [])
        if rids:
            await db.execute(text("DELETE FROM task_results WHERE id = ANY(:r)"), {"r": rids})
            await db.commit()
