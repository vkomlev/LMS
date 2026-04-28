"""Верификация Telegram WebApp initData по HMAC-SHA-256.

Phase Y-1.5: добавлено auto-create user (см. ADR-0021) и
двусторонняя sync users.tg_id ↔ identity_link kind='tg'.
Race-safety: INSERT users + identity_link в SAVEPOINT — IntegrityError
на UNIQUE(kind,value) откатывает только savepoint.
"""
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qsl, unquote

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.users import Users
from app.services.audit_service import log_event
from app.services.auth import identity_link_service

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
    user_str = params.get("user")
    if not user_str:
        return None
    try:
        user = json.loads(unquote(user_str))
        return str(user.get("id"))
    except (ValueError, KeyError):
        return None


def extract_tg_full_name(params: dict) -> str | None:
    """Извлечь full_name из initData.user (first_name + last_name)."""
    user_str = params.get("user")
    if not user_str:
        return None
    try:
        user = json.loads(unquote(user_str))
        first = (user.get("first_name") or "").strip()
        last = (user.get("last_name") or "").strip()
        full = (first + " " + last).strip()
        return full or None
    except (ValueError, KeyError):
        return None


async def get_or_create_user_by_tg(
    db: AsyncSession,
    tg_user_id: int,
    full_name: str | None,
    ip: str | None,
    user_agent: str | None,
) -> tuple[Users, bool]:
    """Найти пользователя по tg-identity или создать нового атомарно.

    Если existing identity найден, но users.tg_id != value — UPDATE users.tg_id (sync).
    При создании — SAVEPOINT(INSERT users(tg_id=tg_user_id) + INSERT identity_link).
    При UNIQUE(kind,value) IntegrityError — savepoint откатывается, мы возвращаем
    existing user (concurrent registration уже прошла). Не создаёт orphan users.
    full_name fallback: если None/empty → 'Гость TG-{last4}'.
    Возвращает (user, created_flag).
    """
    user = await identity_link_service.get_user_by_identity(db, "tg", str(tg_user_id))
    if user is not None:
        if user.tg_id != tg_user_id:
            user.tg_id = tg_user_id
            await db.flush()
        return user, False

    if not full_name:
        full_name = f"Гость TG-{str(tg_user_id)[-4:]}"

    new_user = Users(
        email=None, password_hash=None, full_name=full_name, tg_id=tg_user_id,
    )
    try:
        async with db.begin_nested():
            db.add(new_user)
            await db.flush()
            await identity_link_service.upsert_identity(db, new_user.id, "tg", str(tg_user_id))
    except IntegrityError:
        existing = await identity_link_service.get_user_by_identity(db, "tg", str(tg_user_id))
        if existing is None:
            raise
        logger.info("tg_init: race resolved, reusing existing user_id=%d", existing.id)
        return existing, False

    fn_source = "init_data" if not full_name.startswith("Гость TG-") else "fallback_anonymous"
    await log_event(
        db,
        "user.registered.via_tg_init",
        user_id=new_user.id,
        ip=ip,
        user_agent=user_agent,
        details={
            "identity_kind": "tg",
            "value_masked": "***" + str(tg_user_id)[-4:],
            "full_name_source": fn_source,
        },
    )
    logger.info("user.registered.via_tg_init user_id=%d", new_user.id)
    return new_user, True
