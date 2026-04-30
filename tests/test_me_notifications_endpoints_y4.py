"""Integration HTTP-тесты /me/notifications/* (Phase Y-4)."""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import inbox_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _setup_user_with_session(db):
    email = f"y4-notif-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name="Y4-notif", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    access_token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    return u.id, access_token


async def _cleanup(db, user_id: int):
    await db.execute(text("DELETE FROM notifications WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
    await db.commit()


@pytest.mark.asyncio
async def test_unread_count_requires_auth(client):
    resp = await client.get("/api/v1/me/notifications/unread-count")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_requires_auth(client):
    resp = await client.get("/api/v1/me/notifications")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_mark_read_requires_auth(client):
    resp = await client.post("/api/v1/me/notifications/1/read")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unread_count_returns_correct(db, client):
    uid, token = await _setup_user_with_session(db)
    try:
        # 0 — empty
        resp0 = await client.get(
            "/api/v1/me/notifications/unread-count",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp0.status_code == 200
        assert resp0.json()["count"] == 0

        # Добавим 3 непрочитанных
        for i in range(3):
            await inbox_service.create_for_user(
                db, user_id=uid, kind="sa_com_graded",
                title="t", content=f"c{i}", payload={}, created_by=None,
            )
        await db.commit()

        resp3 = await client.get(
            "/api/v1/me/notifications/unread-count",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp3.json()["count"] == 3
    finally:
        await _cleanup(db, uid)


@pytest.mark.asyncio
async def test_list_pagination_and_unread_only(db, client):
    uid, token = await _setup_user_with_session(db)
    try:
        for i in range(4):
            await inbox_service.create_for_user(
                db, user_id=uid, kind="sa_com_graded",
                title=f"t{i}", content=f"c{i}", payload={}, created_by=None,
            )
        await db.commit()

        resp = await client.get(
            "/api/v1/me/notifications?limit=2&offset=0",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 2
        assert all(item["is_unread"] is True for item in items)
        assert all(item["read_at"] is None for item in items)
    finally:
        await _cleanup(db, uid)


@pytest.mark.asyncio
async def test_mark_read_updates_record(db, client):
    uid, token = await _setup_user_with_session(db)
    try:
        rec = await inbox_service.create_for_user(
            db, user_id=uid, kind="sa_com_graded",
            title="t", content="c", payload={}, created_by=None,
        )
        await db.commit()

        resp = await client.post(
            f"/api/v1/me/notifications/{rec.id}/read",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["read_at"] is not None

        # Audit event записан
        cnt = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM audit_event "
                    "WHERE event_type='student.notification.read' AND user_id=:u"
                ),
                {"u": uid},
            )
        ).scalar()
        assert cnt >= 1
    finally:
        await _cleanup(db, uid)


@pytest.mark.asyncio
async def test_mark_read_idempotent_for_already_read(db, client):
    uid, token = await _setup_user_with_session(db)
    try:
        rec = await inbox_service.create_for_user(
            db, user_id=uid, kind="sa_com_graded",
            title="t", content="c", payload={}, created_by=None,
        )
        await db.commit()

        resp1 = await client.post(
            f"/api/v1/me/notifications/{rec.id}/read",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp1.status_code == 200

        # Второй вызов — idempotent 200
        resp2 = await client.post(
            f"/api/v1/me/notifications/{rec.id}/read",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 200
    finally:
        await _cleanup(db, uid)


@pytest.mark.asyncio
async def test_mark_read_others_record_returns_403(db, client):
    """IDOR защита: чужую запись пометить нельзя — 403."""
    owner_id, _ = await _setup_user_with_session(db)
    other_id, other_token = await _setup_user_with_session(db)
    try:
        rec = await inbox_service.create_for_user(
            db, user_id=owner_id, kind="sa_com_graded",
            title="t", content="c", payload={}, created_by=None,
        )
        await db.commit()

        resp = await client.post(
            f"/api/v1/me/notifications/{rec.id}/read",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert resp.status_code == 403
    finally:
        await _cleanup(db, owner_id)
        await _cleanup(db, other_id)


@pytest.mark.asyncio
async def test_mark_read_nonexistent_returns_404(db, client):
    uid, token = await _setup_user_with_session(db)
    try:
        resp = await client.post(
            "/api/v1/me/notifications/9999999/read",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
    finally:
        await _cleanup(db, uid)
