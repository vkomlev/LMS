"""Integration HTTP-тесты Y-3 /me/* endpoints.

Покрывает:
- GET /me/identities — auth required (401 без), возвращает masked values
- GET /me/courses — auth required, возвращает list с progress (smoke)
- GET /me/last-position — auth required, корректно для пустого state
- GET /me/streak — auth required, today_active flag
- POST /auth/link-token/issue — auth required (401 без), возвращает токен
- POST /me/identity/{kind}/link — 401 на invalid link_token
"""
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service, link_token_service
from app.services.auth.session_service import create_session


async def _setup_user_with_session(db, *, with_email: bool = True):
    """Создать user + email-identity + session, вернуть (user_id, access_token)."""
    email = f"y3http_{random.randint(10**8, 10**10)}@example.com" if with_email else None
    user = Users(email=email, password_hash=None, full_name="Y3-http", tg_id=None)
    db.add(user)
    await db.flush()
    if with_email:
        await identity_link_service.upsert_identity(db, user.id, "email", email)
    access_token, _, _ = await create_session(db, user_id=user.id)
    await db.commit()
    return user.id, access_token, email


async def _cleanup(db, user_id: int) -> None:
    """Cleanup без DELETE FROM users — иначе FK SET NULL на audit_event.user_id
    триггерит audit_event_no_modify (UPDATE = запрещён). Тест-юзеры остаются в БД,
    как и в Y-1 тестах — это приемлемо для dev.
    """
    await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
    await db.commit()


# ── auth required ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_me_identities_requires_auth(client):
    resp = await client.get("/api/v1/me/identities")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_courses_requires_auth(client):
    resp = await client.get("/api/v1/me/courses")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_streak_requires_auth(client):
    resp = await client.get("/api/v1/me/streak")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_last_position_requires_auth(client):
    resp = await client.get("/api/v1/me/last-position")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_link_token_issue_requires_auth(client):
    resp = await client.post("/api/v1/auth/link-token/issue", json={"kind": "vk"})
    assert resp.status_code == 401


# ── smoke happy ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_me_identities_returns_masked_email(db, client):
    user_id, token, email = await _setup_user_with_session(db)
    try:
        resp = await client.get(
            "/api/v1/me/identities",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) >= 1
        email_item = next((i for i in items if i["kind"] == "email"), None)
        assert email_item is not None
        # Маскирование: первые 3 символа local + ***
        assert "***" in email_item["value_masked"]
        assert email.split("@")[1] in email_item["value_masked"]
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_me_courses_returns_empty_for_no_enrollment(db, client):
    user_id, token, _ = await _setup_user_with_session(db)
    try:
        resp = await client.get(
            "/api/v1/me/courses",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_me_last_position_null_for_inactive_user(db, client):
    user_id, token, _ = await _setup_user_with_session(db)
    try:
        resp = await client.get(
            "/api/v1/me/last-position",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() is None
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_me_streak_zero_for_inactive_user(db, client):
    user_id, token, _ = await _setup_user_with_session(db)
    try:
        resp = await client.get(
            "/api/v1/me/streak",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["streak_days"] == 0
        assert body["last_active_date"] is None
        assert body["today_active"] is False
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_link_token_issue_returns_token(db, client):
    user_id, token, _ = await _setup_user_with_session(db)
    try:
        resp = await client.post(
            "/api/v1/auth/link-token/issue",
            json={"kind": "vk"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "link_token" in body
        assert "expires_at" in body
        assert len(body["link_token"]) >= 40
    finally:
        await _cleanup(db, user_id)


# ── /me/identity/{kind}/link негативные сценарии ───────────────────────────

@pytest.mark.asyncio
async def test_link_identity_email_invalid_link_token(db, client):
    user_id, token, _ = await _setup_user_with_session(db)
    try:
        resp = await client.post(
            "/api/v1/me/identity/email/link",
            json={"link_token": "garbage", "magic_link_token": "00" * 32},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_link_identity_tg_invalid_link_token(db, client):
    user_id, token, _ = await _setup_user_with_session(db)
    try:
        resp = await client.post(
            "/api/v1/me/identity/tg/link",
            json={"link_token": "garbage", "init_data": "user=%7B%22id%22%3A1%7D"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_link_identity_vk_invalid_link_token(db, client):
    user_id, token, _ = await _setup_user_with_session(db)
    try:
        resp = await client.post(
            "/api/v1/me/identity/vk/link",
            json={
                "link_token": "garbage",
                "code": "x", "code_verifier": "y", "device_id": "z",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_link_token_with_wrong_user_rejected(db, client):
    """link_token, выпущенный для user A, не принимается user B."""
    from app.services.rate_limit_service import get_redis
    from app.core.config import Settings

    user_a = await _setup_user_with_session(db)
    user_b = await _setup_user_with_session(db)
    try:
        # Прямо выпускаем токен на user A через сервис
        redis = get_redis(Settings().redis_url)
        raw, _ = await link_token_service.issue(redis, user_id=user_a[0], kind="vk")

        # user B пытается использовать
        resp = await client.post(
            "/api/v1/me/identity/vk/link",
            json={
                "link_token": raw,
                "code": "x", "code_verifier": "y", "device_id": "z",
            },
            headers={"Authorization": f"Bearer {user_b[1]}"},
        )
        assert resp.status_code == 401
    finally:
        await _cleanup(db, user_a[0])
        await _cleanup(db, user_b[0])
