"""tsk-298 Фаза 3-Ⅲ: messages (inbox/by-user/send/mark-read) открыты cookie.

ACL:
- inbox/by-user/mark-read: только свои данные (user_id == current_user) / service.
- send: отправитель = сам (без подмены sender_id) + получатель — участник
  переписки (`can_message`: связь teacher↔student / methodist-admin).
Плюс фикс разрыва ученической переписки (была сервис-only → 403 у ученика).
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


async def _user(db, role: str | None = None) -> tuple[int, str]:
    u = Users(email=f"t298m-{random.randint(10**8, 10**10)}@example.com",
              password_hash=None, full_name="t298m", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role:
        await db.execute(
            text("INSERT INTO user_roles (user_id, role_id) SELECT :u, r.id FROM roles r WHERE r.name=:r2 ON CONFLICT DO NOTHING"),
            {"u": u.id, "r2": role},
        )
    await db.commit()
    return u.id, token


async def _link(db, student_id: int, teacher_id: int):
    await db.execute(
        text("INSERT INTO student_teacher_links (student_id, teacher_id) VALUES (:s,:t) ON CONFLICT DO NOTHING"),
        {"s": student_id, "t": teacher_id},
    )
    await db.commit()


async def _cleanup(db, user_ids: list[int]):
    for uid in user_ids:
        await db.execute(text("DELETE FROM messages WHERE sender_id=:u OR recipient_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM student_teacher_links WHERE teacher_id=:u OR student_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": uid})
    await db.commit()


# ── inbox / by-user own-data gate ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_inbox_cookie_self(db, client):
    uid, token = await _user(db)
    try:
        resp = await client.get(f"/api/v1/messages/inbox?user_id={uid}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        assert "items" in resp.json()
    finally:
        await _cleanup(db, [uid])


@pytest.mark.asyncio
async def test_inbox_cookie_no_user_id_defaults_to_self(db, client):
    """Без user_id (как ученический хук) cookie отдаёт свой inbox — фикс разрыва."""
    uid, token = await _user(db)
    try:
        resp = await client.get("/api/v1/messages/inbox", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        assert "items" in resp.json()
    finally:
        await _cleanup(db, [uid])


@pytest.mark.asyncio
async def test_inbox_cookie_foreign_forbidden(db, client):
    uid, token = await _user(db)
    try:
        resp = await client.get(f"/api/v1/messages/inbox?user_id={uid + 99999}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, [uid])


@pytest.mark.asyncio
async def test_by_user_cookie_foreign_forbidden(db, client):
    uid, token = await _user(db)
    try:
        resp = await client.get(f"/api/v1/messages/by-user?user_id={uid + 99999}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, [uid])


# ── send ACL ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_to_linked_peer_ok(db, client):
    """Преподаватель → свой ученик (связь есть) → 201."""
    tid, token = await _user(db, "teacher")
    sid, _ = await _user(db)
    await _link(db, sid, tid)
    try:
        resp = await client.post(
            "/api/v1/messages/send",
            json={"message_type": "text", "content": {"text": "привет"}, "recipient_id": sid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.text
        # sender_id принудительно = отправитель
        assert resp.json()["sender_id"] == tid
    finally:
        await _cleanup(db, [tid, sid])


@pytest.mark.asyncio
async def test_send_to_unrelated_forbidden(db, client):
    """Отправка постороннему (нет связи, не methodist) → 403."""
    tid, token = await _user(db, "teacher")
    other, _ = await _user(db)
    try:
        resp = await client.post(
            "/api/v1/messages/send",
            json={"message_type": "text", "content": {"text": "спам"}, "recipient_id": other},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, [tid, other])


@pytest.mark.asyncio
async def test_send_impersonation_forbidden(db, client):
    """Подмена sender_id на чужой → 403."""
    tid, token = await _user(db, "teacher")
    sid, _ = await _user(db)
    await _link(db, sid, tid)
    try:
        resp = await client.post(
            "/api/v1/messages/send",
            json={"message_type": "text", "content": {"text": "x"}, "recipient_id": sid, "sender_id": sid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, [tid, sid])


@pytest.mark.asyncio
async def test_send_service_bypass(db, client):
    """Сервисный ключ (бот) шлёт с явным sender_id, без ACL → 201."""
    a, _ = await _user(db)
    b, _ = await _user(db)
    api_key = next(iter(_settings.valid_api_keys))
    try:
        resp = await client.post(
            "/api/v1/messages/send",
            json={"message_type": "text", "content": {"text": "sys"}, "recipient_id": b, "sender_id": a},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 201, resp.text
    finally:
        await _cleanup(db, [a, b])


# ── mark-read gate ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_read_foreign_forbidden(db, client):
    uid, token = await _user(db)
    try:
        resp = await client.post(
            "/api/v1/messages/mark-read/by-sender",
            json={"user_id": uid + 99999, "sender_id": uid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, [uid])


# ── peer_id 1:1 фильтр ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_by_user_peer_filter(db, client):
    """peer_id ограничивает переписку конкретным собеседником."""
    tid, token = await _user(db, "teacher")
    a, _ = await _user(db)
    b, _ = await _user(db)
    await _link(db, a, tid)
    await _link(db, b, tid)
    api_key = next(iter(_settings.valid_api_keys))
    try:
        # teacher пишет a и b (через service, чтобы создать данные)
        for peer, txt in ((a, "ha"), (b, "hb")):
            await client.post(
                "/api/v1/messages/send",
                json={"message_type": "text", "content": {"text": txt}, "recipient_id": peer, "sender_id": tid},
                headers={"X-API-Key": api_key},
            )
        # teacher читает свою переписку с a — только с a
        resp = await client.get(
            f"/api/v1/messages/by-user?user_id={tid}&direction=both&peer_id={a}&limit=50",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        peers = {m["recipient_id"] for m in items} | {m["sender_id"] for m in items}
        assert b not in peers, "peer_id=a не должен возвращать переписку с b"
    finally:
        await _cleanup(db, [tid, a, b])
