"""Unit-тесты link_token_service (in-memory mode, без Redis).

Покрывает:
- issue → consume happy path (payload корректный)
- second consume того же токена → LinkTokenError("invalid") (single-use)
- consume пустого токена → invalid
- consume garbage → invalid
- consume чужого raw_token (другой hash) → invalid
- TTL: payload содержит issued_at в UTC; expires_at = +5 мин
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.auth.link_token_service import (
    LinkTokenError,
    TTL,
    _reset_memory_store_for_tests,
    consume,
    issue,
)


@pytest.fixture(autouse=True)
def _reset():
    _reset_memory_store_for_tests()
    yield
    _reset_memory_store_for_tests()


@pytest.mark.asyncio
async def test_issue_returns_token_and_expires_at():
    raw, expires_at = await issue(None, user_id=42, kind="vk")
    assert isinstance(raw, str) and len(raw) >= 40
    delta = expires_at - datetime.now(timezone.utc)
    # TTL = 5 мин ± погрешность создания
    assert timedelta(minutes=4) < delta <= TTL


@pytest.mark.asyncio
async def test_consume_returns_payload_for_each_kind():
    for kind in ("email", "tg", "vk"):
        raw, _ = await issue(None, user_id=7, kind=kind)
        payload = await consume(None, raw)
        assert payload.user_id == 7
        assert payload.kind == kind
        assert payload.issued_at.tzinfo is not None  # timezone-aware


@pytest.mark.asyncio
async def test_consume_is_single_use():
    raw, _ = await issue(None, user_id=1, kind="email")
    await consume(None, raw)
    with pytest.raises(LinkTokenError) as exc:
        await consume(None, raw)
    assert exc.value.reason == "invalid"


@pytest.mark.asyncio
async def test_consume_empty_token_is_invalid():
    with pytest.raises(LinkTokenError) as exc:
        await consume(None, "")
    assert exc.value.reason == "invalid"


@pytest.mark.asyncio
async def test_consume_garbage_is_invalid():
    with pytest.raises(LinkTokenError) as exc:
        await consume(None, "totally-not-a-valid-token-xyz")
    assert exc.value.reason == "invalid"


@pytest.mark.asyncio
async def test_two_tokens_independent():
    raw1, _ = await issue(None, user_id=1, kind="email")
    raw2, _ = await issue(None, user_id=2, kind="vk")
    p1 = await consume(None, raw1)
    p2 = await consume(None, raw2)
    assert p1.user_id == 1 and p1.kind == "email"
    assert p2.user_id == 2 and p2.kind == "vk"
    # После consume оба недоступны
    with pytest.raises(LinkTokenError):
        await consume(None, raw1)
    with pytest.raises(LinkTokenError):
        await consume(None, raw2)
