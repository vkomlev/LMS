"""Верификация Telegram WebApp initData по HMAC-SHA-256."""
import hashlib
import hmac
import logging
import time
from urllib.parse import parse_qsl, unquote

logger = logging.getLogger(__name__)


def verify_tg_init_data(init_data: str, bot_token: str) -> dict | None:
    """
    Проверить подпись initData от Telegram WebApp.
    Возвращает dict с полями initData (включая user) при успехе, None при ошибке.
    """
    params = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = params.pop("hash", None)
    if not received_hash:
        logger.debug("tg_init_data: нет поля hash")
        return None

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(params.items())
    )

    secret_key = hmac.new(
        b"WebAppData", bot_token.encode(), hashlib.sha256
    ).digest()
    expected = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        logger.warning("tg_init_data: неверная подпись")
        return None

    auth_date = params.get("auth_date")
    if auth_date:
        try:
            age = time.time() - int(auth_date)
            if age > 86400:
                logger.warning("tg_init_data: auth_date устарел на %.0f сек", age)
                return None
        except ValueError:
            return None

    return params


def extract_tg_user_id(params: dict) -> str | None:
    """Извлечь tg_id из поля 'user' (JSON-строка)."""
    import json

    user_str = params.get("user")
    if not user_str:
        return None
    try:
        user = json.loads(unquote(user_str))
        return str(user.get("id"))
    except (ValueError, KeyError):
        return None
