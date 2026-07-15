"""Integration HTTP-тесты PATCH /api/v1/me — self-service обновление ФИО (tsk-223).

Покрывает:
- happy path: PATCH обновляет full_name, возвращает его; GET /me затем отдаёт новое;
- 401 без аутентификации;
- 422 на невалидном формате (латиница, цифры, одно слово);
- нормализация (схлопывание пробелов) при обновлении.

Образец подъёма user+session — как в test_me_endpoints_y3.py.
"""
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _setup_user_with_session(db):
    """Создать user + email-identity + session, вернуть (user_id, access_token)."""
    email = f"tsk223_{random.randint(10**8, 10**10)}@example.com"
    user = Users(email=email, password_hash=None, full_name=None, tg_id=None)
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", email)
    access_token, _, _ = await create_session(db, user_id=user.id)
    await db.commit()
    return user.id, access_token


async def _cleanup(db, user_id: int) -> None:
    """Cleanup без DELETE FROM users (FK SET NULL на audit_event → триггер)."""
    await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
    await db.commit()


# ── auth required ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_patch_me_requires_auth(client):
    resp = await client.patch("/api/v1/me", json={"full_name": "Иванов Иван"})
    assert resp.status_code == 401


# ── happy path ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_patch_me_updates_full_name(db, client):
    user_id, token = await _setup_user_with_session(db)
    try:
        resp = await client.patch(
            "/api/v1/me",
            json={"full_name": "Иванова Мария Петровна"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["full_name"] == "Иванова Мария Петровна"

        # GET /me отдаёт обновлённое имя из БД.
        me_resp = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me_resp.status_code == 200
        assert me_resp.json()["full_name"] == "Иванова Мария Петровна"
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_patch_me_normalizes_whitespace(db, client):
    user_id, token = await _setup_user_with_session(db)
    try:
        resp = await client.patch(
            "/api/v1/me",
            json={"full_name": "  Мамин-Сибиряк   Дмитрий  "},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["full_name"] == "Мамин-Сибиряк Дмитрий"
    finally:
        await _cleanup(db, user_id)


# ── 422 invalid format ───────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_value",
    [
        "Viktor Komlev",   # латиница
        "Иванов Иван2",    # цифра
        "Иван",            # одно слово
        "",                # пусто
    ],
)
async def test_patch_me_rejects_invalid_format(db, client, bad_value):
    user_id, token = await _setup_user_with_session(db)
    try:
        resp = await client.patch(
            "/api/v1/me",
            json={"full_name": bad_value},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
    finally:
        await _cleanup(db, user_id)
