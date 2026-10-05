"""tsk-1216: вложение к сообщению переписки из браузера (cookie/Bearer).

Эндпоинты `POST/GET /messages/{id}/attachment` были сервисными: скачивание
доверяло `user_id` из адреса. Проверяем:

1. Отправитель прикладывает файл к своему сообщению, получатель и отправитель
   скачивают его; посторонний — 403 (даже с подставленным `user_id`).
2. Чужое сообщение — 403 на загрузку; повторная загрузка — 409.
3. `/messages/send` от браузера не принимает attachment_id/url из тела.
4. Сервисный ключ работает как раньше (бот).
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

pytestmark = pytest.mark.asyncio

_settings = Settings()
_TAG = "tsk1216"


async def _user(db, role: str | None = None) -> tuple[int, str]:
    """Пользователь с живой сессией (bearer-токен) и необязательной ролью."""
    u = Users(email=f"{_TAG}-{random.randint(10**8, 10**10)}@example.com",
              password_hash=None, full_name=_TAG, tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role:
        await db.execute(
            text("INSERT INTO user_roles (user_id, role_id) SELECT :u, r.id FROM roles r "
                 "WHERE r.name=:r2 ON CONFLICT DO NOTHING"),
            {"u": u.id, "r2": role},
        )
    await db.commit()
    return u.id, token


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _cleanup(db, ids: list[int]) -> None:
    for uid in ids:
        for sql in (
            "DELETE FROM messages WHERE sender_id=:u OR recipient_id=:u",
            "DELETE FROM student_teacher_links WHERE teacher_id=:u OR student_id=:u",
            "DELETE FROM user_session WHERE user_id=:u",
            "DELETE FROM identity_link WHERE user_id=:u",
            "DELETE FROM user_roles WHERE user_id=:u",
        ):
            await db.execute(text(sql), {"u": uid})
    await db.commit()


async def _pair(db):
    tid, ttok = await _user(db, "teacher")
    sid, stok = await _user(db)
    oid, otok = await _user(db)
    await db.execute(
        text("INSERT INTO student_teacher_links (student_id, teacher_id) VALUES (:s,:t) "
             "ON CONFLICT DO NOTHING"),
        {"s": sid, "t": tid},
    )
    await db.commit()
    return (sid, stok), (tid, ttok), (oid, otok)


async def _send(client, token: str, to: int, **extra) -> dict:
    r = await client.post("/api/v1/messages/send", headers=_h(token), json={
        "message_type": "text", "content": {"text": "скрин"}, "recipient_id": to,
        "source_system": "spw", **extra,
    })
    assert r.status_code == 201, r.text
    return r.json()


async def test_attach_and_download_acl(db, client):
    (sid, stok), (tid, ttok), (oid, otok) = await _pair(db)
    try:
        msg = await _send(client, stok, tid)
        up = await client.post(
            f"/api/v1/messages/{msg['id']}/attachment", headers=_h(stok),
            files={"file": ("shot.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
        )
        assert up.status_code == 201, up.text
        assert up.json()["attachment_url"] == f"/api/v1/messages/{msg['id']}/attachment"

        for tok in (stok, ttok):
            r = await client.get(f"/api/v1/messages/{msg['id']}/attachment", headers=_h(tok))
            assert r.status_code == 200, r.text
            assert r.content.startswith(b"\x89PNG")

        r = await client.get(
            f"/api/v1/messages/{msg['id']}/attachment?user_id={tid}", headers=_h(otok)
        )
        assert r.status_code == 403

        again = await client.post(
            f"/api/v1/messages/{msg['id']}/attachment", headers=_h(stok),
            files={"file": ("b.txt", b"x", "text/plain")},
        )
        assert again.status_code == 409

        reply = await _send(client, ttok, sid)
        foreign = await client.post(
            f"/api/v1/messages/{reply['id']}/attachment", headers=_h(stok),
            files={"file": ("b.txt", b"x", "text/plain")},
        )
        assert foreign.status_code == 403
    finally:
        await _cleanup(db, [sid, tid, oid])


async def test_send_ignores_attachment_from_body(db, client):
    (sid, stok), (tid, _), (oid, _) = await _pair(db)
    try:
        msg = await _send(client, stok, tid, attachment_id="1_x_foreign.png",
                          attachment_url="/api/v1/messages/1/attachment")
        assert msg["attachment_id"] is None
        assert msg["attachment_url"] is None
    finally:
        await _cleanup(db, [sid, tid, oid])


async def test_service_key_unchanged(db, client):
    (sid, stok), (tid, _), (oid, _) = await _pair(db)
    key = next(iter(_settings.valid_api_keys))
    try:
        msg = await _send(client, stok, tid)
        up = await client.post(
            f"/api/v1/messages/{msg['id']}/attachment?api_key={key}",
            files={"file": ("a.txt", b"hello", "text/plain")},
        )
        assert up.status_code == 201, up.text
        ok = await client.get(f"/api/v1/messages/{msg['id']}/attachment?api_key={key}&user_id={tid}")
        assert ok.status_code == 200
        bad = await client.get(f"/api/v1/messages/{msg['id']}/attachment?api_key={key}&user_id={oid}")
        assert bad.status_code == 403
    finally:
        await _cleanup(db, [sid, tid, oid])
