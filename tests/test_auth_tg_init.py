"""
Тесты верификации Telegram initData HMAC.

Покрывает:
- verify_tg_init_data: happy path, неверная подпись, устаревший auth_date
- extract_tg_user_id: корректный JSON, отсутствующий user
- POST /auth/tg/init: невалидная подпись → 401
"""
import hashlib
import hmac
import json
import time
import urllib.parse

import pytest


def _make_init_data(tg_id: int, bot_token: str, auth_date: int | None = None) -> str:
    """Сформировать корректный initData с подписью."""
    if auth_date is None:
        auth_date = int(time.time())
    user = json.dumps({"id": tg_id, "first_name": "Test"})
    params = {
        "auth_date": str(auth_date),
        "user": user,
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    sig = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    params["hash"] = sig
    return urllib.parse.urlencode(params)


def test_verify_tg_init_data_happy():
    """Корректная подпись должна вернуть dict с параметрами."""
    from app.services.auth.tg_init_service import verify_tg_init_data

    bot_token = "123456:TEST_BOT_TOKEN"
    init_data = _make_init_data(tg_id=12345, bot_token=bot_token)
    result = verify_tg_init_data(init_data, bot_token)
    assert result is not None
    assert "auth_date" in result
    assert "user" in result


def test_verify_tg_init_data_wrong_signature():
    """Неверная подпись → None."""
    from app.services.auth.tg_init_service import verify_tg_init_data

    init_data = _make_init_data(tg_id=12345, bot_token="real_token")
    result = verify_tg_init_data(init_data, "wrong_token")
    assert result is None


def test_verify_tg_init_data_expired_auth_date():
    """auth_date старше 24 часов → None."""
    from app.services.auth.tg_init_service import verify_tg_init_data

    bot_token = "123456:TEST_BOT_TOKEN"
    old_ts = int(time.time()) - 90000
    init_data = _make_init_data(tg_id=12345, bot_token=bot_token, auth_date=old_ts)
    result = verify_tg_init_data(init_data, bot_token)
    assert result is None


def test_verify_tg_init_data_missing_hash():
    """Отсутствие hash → None."""
    from app.services.auth.tg_init_service import verify_tg_init_data

    init_data = "auth_date=1234567890&user=%7B%22id%22%3A1%7D"
    assert verify_tg_init_data(init_data, "any_token") is None


def test_extract_tg_user_id_happy():
    """Корректный JSON user → строка с id."""
    from app.services.auth.tg_init_service import extract_tg_user_id

    params = {"user": json.dumps({"id": 99999, "first_name": "Test"})}
    assert extract_tg_user_id(params) == "99999"


def test_extract_tg_user_id_no_user():
    """Отсутствие user → None."""
    from app.services.auth.tg_init_service import extract_tg_user_id

    assert extract_tg_user_id({}) is None


@pytest.mark.asyncio
async def test_tg_init_endpoint_invalid_signature(client):
    """POST /auth/tg/init с невалидной подписью → 401 или 503 (если токен не задан)."""
    resp = await client.post(
        "/api/v1/auth/tg/init",
        json={"init_data": "auth_date=1234&hash=fakehash"},
    )
    assert resp.status_code in (401, 503)
