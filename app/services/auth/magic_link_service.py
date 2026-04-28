"""Сервис одноразовых email magic-link."""
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.magic_link import MagicLink

logger = logging.getLogger(__name__)

_TOKEN_BYTES = 32
_TTL_MINUTES = 15
_RESEND_API_URL = "https://api.resend.com/emails"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(raw: bytes) -> bytes:
    return hashlib.sha256(raw).digest()


async def create_magic_link(
    db: AsyncSession,
    email: str,
) -> str:
    """Создать одноразовый токен и сохранить hash; вернуть raw hex-токен."""
    raw = os.urandom(_TOKEN_BYTES)
    token_hash = _hash_token(raw)
    link = MagicLink(
        email=email,
        token_hash=token_hash,
        expires_at=_now() + timedelta(minutes=_TTL_MINUTES),
    )
    db.add(link)
    await db.flush()
    return raw.hex()


async def send_magic_link_email(
    token: str,
    email: str,
    settings: Settings,
) -> None:
    """Отправить magic-link через Resend API."""
    link_url = f"{settings.public_base_url}/auth/magic-link/consume?token={token}"

    if not settings.resend_api_key:
        # Dev-fallback: Resend не настроен — логируем готовую ссылку,
        # оператор видит её в LMS stdout и переходит вручную.
        # В prod присутствие RESEND_API_KEY обязательно (см. preflight ТЗ Y-1 §15).
        logger.warning(
            "RESEND_API_KEY не задан — письмо не отправлено. DEV magic-link для %s: %s",
            email, link_url,
        )
        return

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.smtp_from,
                "to": [email],
                "subject": "Вход на платформу",
                "html": (
                    f"<p>Для входа перейдите по ссылке (действительна {_TTL_MINUTES} мин):</p>"
                    f'<p><a href="{link_url}">{link_url}</a></p>'
                ),
            },
        )
    if resp.status_code >= 400:
        logger.error("Resend API error %s: %s", resp.status_code, resp.text)
        raise RuntimeError("Ошибка отправки email")


async def consume_magic_link(
    db: AsyncSession,
    token: str,
) -> "MagicLink | None":
    """
    Найти и погасить magic_link по raw hex-токену.
    Возвращает MagicLink если валиден, None если не найден или истёк или уже использован.
    """
    try:
        raw = bytes.fromhex(token)
    except ValueError:
        return None
    token_hash = _hash_token(raw)
    result = await db.execute(
        select(MagicLink).where(
            MagicLink.token_hash == token_hash,
            MagicLink.consumed_at.is_(None),
            MagicLink.expires_at > _now(),
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        return None
    link.consumed_at = _now()
    await db.flush()
    return link
