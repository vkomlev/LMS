"""
Integration тесты для magic-link flow.

Покрывает:
- POST /auth/magic-link/send (rate limit pass, невалидный email)
- POST /auth/magic-link/verify (happy path требует реального пользователя с email)
- Replay атака: повторный verify того же токена → 401
- Expired токен → 401
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_magic_link_send_valid_email(client: AsyncClient, monkeypatch):
    """202 на корректный email; фактическая отправка через Resend в юнит-тест не входит.

    Реальный `RESEND_API_KEY` в dev `.env` шлёт письмо на @example.com и получает
    422 от Resend (домен не верифицирован под sandbox) — RuntimeError из
    background task всплывает в ASGITransport и валит тест. Дальше в этом файле
    (happy path verify, replay, expired) отправка не участвует вовсе — там важен
    только сам факт создания токена, поэтому не отправлять её здесь безопасно.
    """
    from app.api.v1.auth import magic_link as magic_link_router

    monkeypatch.setattr(magic_link_router._settings, "resend_api_key", "")

    resp = await client.post(
        "/api/v1/auth/magic-link/send",
        json={"email": "testuser@example.com"},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["message"] == "Письмо отправлено"


@pytest.mark.asyncio
async def test_magic_link_send_invalid_email(client: AsyncClient):
    """422 на невалидный email."""
    resp = await client.post(
        "/api/v1/auth/magic-link/send",
        json={"email": "not-an-email"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_magic_link_verify_invalid_token(client: AsyncClient):
    """401 при попытке verify с несуществующим токеном."""
    resp = await client.post(
        "/api/v1/auth/magic-link/verify",
        json={"token": "a" * 64},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_magic_link_verify_malformed_token(client: AsyncClient):
    """401 при токене с неверным форматом (не hex)."""
    resp = await client.post(
        "/api/v1/auth/magic-link/verify",
        json={"token": "not-a-hex-token!!"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_magic_link_replay_attack(client: AsyncClient, db):
    """Повторный consume того же токена → 401 (consumed_at уже выставлен)."""
    from datetime import datetime, timezone, timedelta
    from app.models.magic_link import MagicLink
    import os, hashlib

    raw = os.urandom(32)
    token_hex = raw.hex()
    token_hash = hashlib.sha256(raw).digest()

    link = MagicLink(
        email="replay@example.com",
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        consumed_at=datetime.now(timezone.utc),
    )
    db.add(link)
    await db.flush()
    await db.commit()

    resp = await client.post(
        "/api/v1/auth/magic-link/verify",
        json={"token": token_hex},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_magic_link_expired_token(client: AsyncClient, db):
    """Токен с истёкшим expires_at → 401."""
    from datetime import datetime, timezone, timedelta
    from app.models.magic_link import MagicLink
    import os, hashlib

    raw = os.urandom(32)
    token_hex = raw.hex()
    token_hash = hashlib.sha256(raw).digest()

    link = MagicLink(
        email="expired@example.com",
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db.add(link)
    await db.flush()
    await db.commit()

    resp = await client.post(
        "/api/v1/auth/magic-link/verify",
        json={"token": token_hex},
    )
    assert resp.status_code == 401
