"""tsk-1338: POST /me/notifications/read-all — сброс счётчика при просмотре ленты.

Лента отдаёт страницу в 50 записей, а непрочитанные копились глубже (у оператора
899 при 13 видимых) — поштучная отметка счётчик не обнуляла.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services import inbox_service
from tests.test_me_notifications_endpoints_y4 import _cleanup, _setup_user_with_session


async def _add(db, uid: int, n: int) -> None:
    for i in range(n):
        await inbox_service.create_for_user(
            db, user_id=uid, kind="sa_com_graded",
            title="t", content=f"c{i}", payload={}, created_by=None,
        )
    await db.commit()


@pytest.mark.asyncio
async def test_read_all_requires_auth(client):
    resp = await client.post("/api/v1/me/notifications/read-all", json={})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_read_all_marks_beyond_first_page_and_only_own(db, client):
    uid, token = await _setup_user_with_session(db)
    other, _ = await _setup_user_with_session(db)
    try:
        await _add(db, uid, 60)
        await _add(db, other, 2)
        h = {"Authorization": f"Bearer {token}"}
        resp = await client.post("/api/v1/me/notifications/read-all", json={}, headers=h)
        assert resp.status_code == 200
        assert resp.json()["marked"] == 60
        cnt = await client.get("/api/v1/me/notifications/unread-count", headers=h)
        assert cnt.json()["count"] == 0
        left = await db.execute(
            text("SELECT count(*) FROM notifications WHERE user_id=:u AND read_at IS NULL"),
            {"u": other},
        )
        assert left.scalar() == 2
    finally:
        await _cleanup(db, uid)
        await _cleanup(db, other)


@pytest.mark.asyncio
async def test_read_all_up_to_keeps_newer(db, client):
    uid, token = await _setup_user_with_session(db)
    try:
        await _add(db, uid, 2)
        await db.execute(
            text("UPDATE notifications SET modified_at = now() + interval '1 hour' "
                 "WHERE id = (SELECT max(id) FROM notifications WHERE user_id=:u)"),
            {"u": uid},
        )
        await db.commit()
        h = {"Authorization": f"Bearer {token}"}
        up_to = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
        resp = await client.post(
            "/api/v1/me/notifications/read-all", json={"up_to": up_to}, headers=h
        )
        assert resp.json()["marked"] == 1
        cnt = await client.get("/api/v1/me/notifications/unread-count", headers=h)
        assert cnt.json()["count"] == 1
    finally:
        await _cleanup(db, uid)
