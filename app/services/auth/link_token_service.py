"""Сервис one-time link_token для привязки identity к существующему user (Phase Y-3).

Используется в `POST /auth/link-token/issue` → `POST /me/identity/{kind}/link`
flow: текущий user выпускает короткоживущий токен, передаёт его внешнему провайдеру
(VK через `state="link:<token>"`, TG initData, magic-link), а затем consume на привязке.

Хранение:
- Production: Redis обязателен. Key — `link_token:{sha256(raw_token)}`. Value — JSON
  payload. TTL 5 мин (SETEX). Consume — атомарный Lua-скрипт GET+DEL (single-use).
  При недоступности Redis в production → `LinkTokenServiceUnavailableError`
  (fail-secure, see Y-3.1 closure of techlead-review S2-1).
- Dev fallback: in-memory dict (single-process only). Активируется только при
  `Settings.env != "production"` AND Redis недоступен.

Безопасность:
- Хранится только sha256-хеш raw token; raw уходит клиенту единожды.
- Three failure modes (invalid / expired / consumed) маппятся в один 401 для клиента —
  не сигналим атакующему о существовании токена.
- В payload есть `issued_at` для forensics в audit_event.

См. tech-spec Y-3 (LMS backend) §5.5, §5.6, §7.3 и ADR-0021 §«Confirmed registration policy».
"""
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Literal

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

LinkTokenKind = Literal["email", "tg", "vk"]
TTL = timedelta(minutes=5)
TTL_SECONDS = int(TTL.total_seconds())


def _is_production() -> bool:
    """Production-режим определяется по env var `ENV=production`.

    Settings прочитаны через os.getenv напрямую, чтобы не зависеть от construction
    Settings() в этом low-level сервисе (избегаем circular imports + heavy init
    при тестах in-memory mode).
    """
    return os.environ.get("ENV", "dev").lower() == "production"

# Atomic GET+DEL: Redis выполняет скрипты атомарно per-key.
_LUA_GET_DEL = """
local v = redis.call('GET', KEYS[1])
if v then
    redis.call('DEL', KEYS[1])
end
return v
"""

# In-memory fallback (DEV ONLY — single-process). Tuple = (payload_json, expires_epoch).
_memory_store: dict[str, tuple[str, float]] = {}


class LinkTokenError(Exception):
    """Базовая ошибка consume link_token: invalid / expired / consumed.

    Все три случая для клиента — единый 401, чтобы не давать сигнал атакующему
    о существовании токена.
    """

    def __init__(self, reason: Literal["invalid", "expired", "consumed"]) -> None:
        self.reason = reason
        super().__init__(f"link_token_{reason}")


class LinkTokenServiceUnavailableError(Exception):
    """Storage backend (Redis) недоступен в production.

    Возникает когда `ENV=production` AND Redis выдал ошибку при issue/consume —
    fail-secure вместо in-memory fallback (Y-3.1 закрытие S2-1). Endpoint должен
    маппить в HTTP 503.
    """


@dataclass(frozen=True)
class LinkTokenPayload:
    """Расшифрованный payload link_token."""

    user_id: int
    kind: LinkTokenKind
    issued_at: datetime


def _hash_token(raw: str) -> str:
    return sha256(raw.encode("utf-8")).hexdigest()


def _key(token_hash: str) -> str:
    return f"link_token:{token_hash}"


async def issue(
    redis: aioredis.Redis | None,
    user_id: int,
    kind: LinkTokenKind,
) -> tuple[str, datetime]:
    """Выпустить one-time link_token. Возвращает (raw_token, expires_at).

    raw_token — base64url, 32 байта энтропии (`secrets.token_urlsafe(32)` → ~43 символа).
    Возвращается клиенту единожды; хранится только sha256-хеш.
    """
    raw = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw)
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + TTL
    payload_json = json.dumps(
        {
            "user_id": user_id,
            "kind": kind,
            "issued_at": issued_at.isoformat(),
        }
    )
    await _store(redis, _key(token_hash), payload_json)
    return raw, expires_at


async def consume(
    redis: aioredis.Redis | None,
    raw_token: str,
) -> LinkTokenPayload:
    """Atomic GET+DEL. Возвращает payload или raise LinkTokenError.

    Single-use семантика: повторный consume того же raw_token → LinkTokenError("invalid").
    """
    if not raw_token:
        raise LinkTokenError("invalid")
    token_hash = _hash_token(raw_token)
    payload_json = await _pop(redis, _key(token_hash))
    if payload_json is None:
        raise LinkTokenError("invalid")
    try:
        data = json.loads(payload_json)
        return LinkTokenPayload(
            user_id=int(data["user_id"]),
            kind=data["kind"],
            issued_at=datetime.fromisoformat(data["issued_at"]),
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        logger.exception("link_token payload corrupted hash=%s...", token_hash[:8])
        raise LinkTokenError("invalid")


async def _store(redis: aioredis.Redis | None, key: str, payload_json: str) -> None:
    """Сохранить payload с TTL. Redis (SETEX) → fallback in-memory (только dev).

    В production при недоступности Redis raises LinkTokenServiceUnavailableError —
    fail-secure вместо незаметной деградации (Y-3.1 / techlead S2-1).
    """
    if redis is not None:
        try:
            await redis.set(key, payload_json, ex=TTL_SECONDS)
            return
        except Exception:
            if _is_production():
                logger.error(
                    "link_token: Redis недоступен на issue в PRODUCTION — fail-secure"
                )
                raise LinkTokenServiceUnavailableError(
                    "link_token storage недоступен"
                )
            logger.warning(
                "link_token: Redis недоступен на issue, fallback in-memory (DEV ONLY)"
            )
    _purge_expired_memory()
    _memory_store[key] = (payload_json, time.time() + TTL_SECONDS)


async def _pop(redis: aioredis.Redis | None, key: str) -> str | None:
    """Atomic GET+DEL. Redis (Lua) → fallback in-memory (только dev).

    В production при недоступности Redis raises LinkTokenServiceUnavailableError.
    """
    if redis is not None:
        try:
            value = await redis.eval(_LUA_GET_DEL, 1, key)
            return value
        except Exception:
            if _is_production():
                logger.error(
                    "link_token: Redis недоступен на consume в PRODUCTION — fail-secure"
                )
                raise LinkTokenServiceUnavailableError(
                    "link_token storage недоступен"
                )
            logger.warning(
                "link_token: Redis недоступен на consume, fallback in-memory (DEV ONLY)"
            )
    rec = _memory_store.pop(key, None)
    if rec is None:
        return None
    payload_json, expires = rec
    if time.time() >= expires:
        return None
    return payload_json


def _purge_expired_memory() -> None:
    """Удалить устаревшие записи in-memory (вызывается при каждом issue)."""
    now = time.time()
    expired = [k for k, (_, exp) in _memory_store.items() if exp < now]
    for k in expired:
        del _memory_store[k]


def _reset_memory_store_for_tests() -> None:
    """Очистить in-memory store. Только для unit-тестов."""
    _memory_store.clear()
