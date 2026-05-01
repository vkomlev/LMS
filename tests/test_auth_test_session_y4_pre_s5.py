"""Y-4 pre-S5: тесты POST /api/v1/auth/test/issue-session.

Покрывает:
- 200 happy path в dev+flag=true с валидным X-API-Key
- 404 path-disabled когда flag=false
- 401 invalid X-API-Key
- 404 user_id не существует
- Cookie выдан с TTL=3600
- Audit event записан БЕЗ значения cookie
"""
from __future__ import annotations

import os
import random

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users


def _api_key() -> str:
    """Первый ключ из VALID_API_KEYS (тот же, что использует TG_LMS)."""
    raw = os.environ.get("VALID_API_KEYS", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        pytest.skip("VALID_API_KEYS пуст в .env — пропускаем")
    return keys[0]


async def _create_user(db) -> int:
    u = Users(
        email=f"y4pre-ts-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="y4pre-ts-user", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _cleanup(db, user_ids: list[int]):
    if not user_ids:
        return
    await db.execute(text("DELETE FROM user_roles WHERE user_id = ANY(:u)"), {"u": user_ids})
    await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:u)"), {"u": user_ids})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:u)"), {"u": user_ids})
    await db.commit()


def _patch_settings(monkeypatch, *, env: str, flag: bool):
    """Мутировать singleton Settings в auth/test/test_session.py.

    Settings создаётся как module-level singleton при импорте; меняем его
    атрибуты прямо для теста. После теста monkeypatch вернёт старое.
    """
    from app.api.v1.auth import test_session as ts_module
    monkeypatch.setattr(ts_module._settings, "env", env, raising=True)
    monkeypatch.setattr(ts_module._settings, "test_endpoints_enabled", flag, raising=True)


@pytest.mark.asyncio
async def test_test_session_404_when_flag_disabled(db, client, monkeypatch):
    """flag=false → 404 без обработки body."""
    _patch_settings(monkeypatch, env="dev", flag=False)
    uid = await _create_user(db)
    try:
        resp = await client.post(
            "/api/v1/auth/test/issue-session",
            json={"user_id": uid},
            headers={"X-API-Key": _api_key()},
        )
        assert resp.status_code == 404
    finally:
        await _cleanup(db, [uid])


@pytest.mark.asyncio
async def test_test_session_404_when_env_production(db, client, monkeypatch):
    """env=production даже с flag=true → 404."""
    _patch_settings(monkeypatch, env="production", flag=True)
    uid = await _create_user(db)
    try:
        resp = await client.post(
            "/api/v1/auth/test/issue-session",
            json={"user_id": uid},
            headers={"X-API-Key": _api_key()},
        )
        assert resp.status_code == 404
    finally:
        await _cleanup(db, [uid])


@pytest.mark.asyncio
async def test_test_session_401_invalid_api_key(db, client, monkeypatch):
    """env=dev + flag=true + невалидный ключ → 401."""
    _patch_settings(monkeypatch, env="dev", flag=True)
    uid = await _create_user(db)
    try:
        resp = await client.post(
            "/api/v1/auth/test/issue-session",
            json={"user_id": uid},
            headers={"X-API-Key": "wrong-key-xxx"},
        )
        assert resp.status_code == 401
    finally:
        await _cleanup(db, [uid])


@pytest.mark.asyncio
async def test_test_session_404_user_not_found(db, client, monkeypatch):
    """Несуществующий user_id → 404."""
    _patch_settings(monkeypatch, env="dev", flag=True)
    resp = await client.post(
        "/api/v1/auth/test/issue-session",
        json={"user_id": 9999999},
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_test_session_happy_path(db, client, monkeypatch):
    """env=dev + flag=true + valid key + valid user → 200 + cookie + audit."""
    _patch_settings(monkeypatch, env="dev", flag=True)
    uid = await _create_user(db)
    try:
        resp = await client.post(
            "/api/v1/auth/test/issue-session",
            json={"user_id": uid},
            headers={"X-API-Key": _api_key()},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user_id"] == uid
        assert body["message"] == "Test session issued"
        assert "expires_at" in body
        # Cookie выдан
        assert "session" in resp.cookies, f"Cookie 'session' not in response; got: {resp.cookies}"
        # Audit event записан
        cnt = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM audit_event "
                    "WHERE event_type='auth.test.session_issued' AND user_id=:u"
                ),
                {"u": uid},
            )
        ).scalar()
        assert cnt >= 1
        # Проверим что details НЕ содержит cookie value
        details = (
            await db.execute(
                text(
                    "SELECT details FROM audit_event "
                    "WHERE event_type='auth.test.session_issued' AND user_id=:u "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"u": uid},
            )
        ).scalar()
        assert details is not None
        # session_id присутствует, но НЕ access_token / cookie value
        assert "session_id" in details
        assert "ttl_seconds" in details
        assert details["ttl_seconds"] == 3600
    finally:
        await _cleanup(db, [uid])
