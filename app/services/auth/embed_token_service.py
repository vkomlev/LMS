"""Сервис генерации и проверки embed URL-token (Phase Y-5).

JWT (HS256) с jti + Redis single-use marker. JWT даёт self-contained
payload (course_uid + external_uid без отдельного БД-lookup на каждом
read), single-use enforce — через Redis (атомарный delete jti при первом
read; повторный read получает 401 token_consumed).

Secret в `.env` `CB_EMBED_JWT_SECRET` (≥32 bytes random base64).
TTL по умолчанию 300 сек (5 мин), управляется `CB_EMBED_JWT_TTL_SEC`.

См. tech-spec Y-5 §6.3 + §13 G1/G9.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_JWT_ALGO = "HS256"
_JWT_SUB = "embed"


class EmbedTokenError(Exception):
    """Базовое исключение для embed-token (invalid/expired/consumed)."""


class EmbedTokenInvalid(EmbedTokenError):
    """JWT невалиден / истёк / claims не совпадают."""


class EmbedTokenConsumed(EmbedTokenError):
    """JWT уже был использован (single-use enforced)."""


class EmbedSecretMissing(EmbedTokenError):
    """CB_EMBED_JWT_SECRET не настроен — fail-secure 503."""


@dataclass(slots=True)
class EmbedTokenIssued:
    """Результат `issue_token`."""

    token: str
    expires_at: datetime
    jti: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _redis_jti_key(jti: str) -> str:
    return f"embed_jti:{jti}"


async def issue_token(
    redis: aioredis.Redis,
    secret: str,
    course_uid: str,
    external_uid: str,
    ttl_sec: int = 300,
) -> EmbedTokenIssued:
    """Сгенерировать одноразовый JWT для embed-iframe.

    Записывает `jti` в Redis с TTL = ttl_sec (single-use marker; удаляется
    атомарно при первом read через `consume_token`).
    """
    if not secret:
        raise EmbedSecretMissing("CB_EMBED_JWT_SECRET не настроен")

    issued_at = _now()
    expires_at = issued_at + timedelta(seconds=ttl_sec)
    jti = str(uuid.uuid4())

    payload: dict[str, object] = {
        "sub": _JWT_SUB,
        "course_uid": course_uid,
        "external_uid": external_uid,
        "jti": jti,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, secret, algorithm=_JWT_ALGO)

    # SET NX EX: защита от race с одинаковым jti (uuid4 коллизии практически
    # невозможны, но fail-secure если они случатся).
    setnx_ok = await redis.set(_redis_jti_key(jti), "1", ex=ttl_sec, nx=True)
    if not setnx_ok:
        logger.warning("embed_token: jti collision — повторная генерация")
        return await issue_token(redis, secret, course_uid, external_uid, ttl_sec)

    return EmbedTokenIssued(token=token, expires_at=expires_at, jti=jti)


async def consume_token(
    redis: aioredis.Redis,
    secret: str,
    token: str,
    expected_course_uid: str,
    expected_external_uid: str,
) -> dict[str, object]:
    """Декодировать и атомарно «израсходовать» embed-token.

    Returns:
        Декодированный payload (без модификаций).

    Raises:
        EmbedSecretMissing: когда secret не настроен.
        EmbedTokenInvalid: когда JWT невалиден / истёк / claims не совпадают.
        EmbedTokenConsumed: когда jti уже был использован.
    """
    if not secret:
        raise EmbedSecretMissing("CB_EMBED_JWT_SECRET не настроен")

    try:
        payload = jwt.decode(token, secret, algorithms=[_JWT_ALGO])
    except jwt.ExpiredSignatureError as exc:
        raise EmbedTokenInvalid("Токен истёк") from exc
    except jwt.InvalidTokenError as exc:
        raise EmbedTokenInvalid("Токен недействителен") from exc

    if payload.get("sub") != _JWT_SUB:
        raise EmbedTokenInvalid("Токен предназначен для другого scope")
    if payload.get("course_uid") != expected_course_uid:
        raise EmbedTokenInvalid("course_uid в токене не совпадает с URL")
    if payload.get("external_uid") != expected_external_uid:
        raise EmbedTokenInvalid("external_uid в токене не совпадает с URL")

    jti = str(payload.get("jti") or "")
    if not jti:
        raise EmbedTokenInvalid("В токене отсутствует jti")

    # Атомарный single-use: DEL возвращает количество удалённых ключей.
    # Если 0 — токен уже потреблён (race с другим запросом или повтор).
    deleted: int = await redis.delete(_redis_jti_key(jti))
    if not deleted:
        raise EmbedTokenConsumed("Токен уже использован")

    return payload
