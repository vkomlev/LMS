"""tsk-298 Фаза 3-Ⅰ: workload + ростер открыты cookie-преподавателю.

Проверяем enabler'ы (`get_db` → `get_current_user` + identity-гейт):
- по cookie своего teacher_id → 200;
- по cookie чужого teacher_id → 403;
- по сервисному ключу (TG-бот) → 200 (backward compat).
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_settings = Settings()


async def _teacher_with_session(db) -> tuple[int, str]:
    u = Users(
        email=f"t298w-tea-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="t298w-tea", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, r.id FROM roles r WHERE r.name = 'teacher' ON CONFLICT DO NOTHING"
        ),
        {"u": u.id},
    )
    await db.commit()
    return u.id, token


async def _student(db) -> int:
    u = Users(
        email=f"t298w-stu-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="t298w-stu", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _link(db, student_id: int, teacher_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO student_teacher_links (student_id, teacher_id) "
            "VALUES (:s, :t) ON CONFLICT DO NOTHING"
        ),
        {"s": student_id, "t": teacher_id},
    )
    await db.commit()


async def _cleanup(db, user_ids: list[int]) -> None:
    for uid in user_ids:
        await db.execute(text("DELETE FROM student_teacher_links WHERE teacher_id=:u OR student_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": uid})
    await db.commit()


# ── workload ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_workload_cookie_self(db, client):
    tid, token = await _teacher_with_session(db)
    try:
        resp = await client.get(
            f"/api/v1/teacher/workload?teacher_id={tid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "pending_manual_reviews_total" in body
        assert "open_help_requests_total" in body
    finally:
        await _cleanup(db, [tid])


@pytest.mark.asyncio
async def test_workload_cookie_foreign_forbidden(db, client):
    tid, token = await _teacher_with_session(db)
    try:
        resp = await client.get(
            f"/api/v1/teacher/workload?teacher_id={tid + 987654}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, [tid])


@pytest.mark.asyncio
async def test_workload_service_key_bypass(db, client):
    tid, _ = await _teacher_with_session(db)
    api_key = next(iter(_settings.valid_api_keys))
    try:
        resp = await client.get(
            f"/api/v1/teacher/workload?teacher_id={tid}",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200, resp.text
    finally:
        await _cleanup(db, [tid])


# ── ростер ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_roster_cookie_self_lists_linked_students(db, client):
    tid, token = await _teacher_with_session(db)
    sid = await _student(db)
    await _link(db, sid, tid)
    try:
        resp = await client.get(
            f"/api/v1/users/{tid}/students",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        ids = [u["id"] for u in resp.json()]
        assert sid in ids
    finally:
        await _cleanup(db, [tid, sid])


@pytest.mark.asyncio
async def test_roster_cookie_foreign_forbidden(db, client):
    tid, token = await _teacher_with_session(db)
    try:
        resp = await client.get(
            f"/api/v1/users/{tid + 987654}/students",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, [tid])


@pytest.mark.asyncio
async def test_roster_service_key_bypass(db, client):
    tid, _ = await _teacher_with_session(db)
    api_key = next(iter(_settings.valid_api_keys))
    try:
        resp = await client.get(
            f"/api/v1/users/{tid}/students",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200, resp.text
    finally:
        await _cleanup(db, [tid])
