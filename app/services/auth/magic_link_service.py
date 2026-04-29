"""Сервис одноразовых email magic-link.

Phase Y-1.5: добавлено auto-create user при verify (см. ADR-0021).
Race-safety: вставка user идёт в SAVEPOINT (db.begin_nested) — IntegrityError
на partial unique index откатывает только nested-транзакцию, не основную,
поэтому magic_link.consumed_at сохраняется.
"""
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.magic_link import MagicLink
from app.models.users import Users
from app.services.audit_service import log_event
from app.services.auth import identity_link_service
from app.services.auth.exceptions import IdentityConflictError

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
    link_mode: bool = False,
) -> None:
    """Отправить magic-link через Resend API.

    `link_mode=True` (Phase Y-3): добавляет query-параметр `link_mode=1` в callback URL —
    SPW использует его, чтобы переключить страницу /auth/magic-link/consume в режим
    привязки email к существующему аккаунту вместо логина.
    """
    suffix = "&link_mode=1" if link_mode else ""
    link_url = f"{settings.public_base_url}/auth/magic-link/consume?token={token}{suffix}"

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


async def peek_magic_link(
    db: AsyncSession,
    token: str,
) -> "MagicLink | None":
    """Phase Y-3: проверить magic_link БЕЗ consume.

    Используется в /auth/magic-link/verify {link_mode=True} — подтверждает
    владение email, но не помечает токен использованным; consume произойдёт
    в /me/identity/email/link при фактической привязке identity.

    Возвращает MagicLink если валиден (не consumed, не expired), иначе None.
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
    return result.scalar_one_or_none()


def _mask_email(email: str) -> str:
    """Маскировать email для audit_event details: первые 3 + *** + домен."""
    if "@" not in email:
        return email[:3] + "***"
    local, domain = email.split("@", 1)
    return local[:3] + "***@" + domain


async def get_or_create_user_by_email(
    db: AsyncSession,
    email: str,
    ip: str | None,
    user_agent: str | None,
) -> tuple[Users, bool]:
    """Найти пользователя по email-identity или создать нового атомарно.

    Email уже должен быть нормализован (lowercase) до вызова.
    Race-safe: INSERT users + identity_link выполняется в SAVEPOINT через
    db.begin_nested(). При IntegrityError (concurrent partial unique violation)
    откатывается только nested savepoint — magic_link.consumed_at в основной
    транзакции сохраняется. Возвращает (user, created_flag).
    """
    user = await identity_link_service.get_user_by_identity(db, "email", email)
    if user is not None:
        return user, False

    # S2 hotfix per handoff 2026-04-28 §2: orphan email
    # (users.email exists без identity_link kind='email').
    # Без проверки INSERT users(email=X) упадёт на partial unique
    # внутри savepoint, dispatcher вернул бы 500.
    # ADR-0021 §2 → 409 identity_conflict.
    orphan = (await db.execute(
        select(Users).where(func.lower(Users.email) == email.lower())
    )).scalar_one_or_none()
    if orphan is not None:
        raise IdentityConflictError(
            conflict_kind="email_already_linked_to_orphan_user",
            existing_kinds=[],
        )

    new_user = Users(email=email, password_hash=None, full_name=None, tg_id=None)
    try:
        async with db.begin_nested():
            db.add(new_user)
            await db.flush()
            await identity_link_service.upsert_identity(db, new_user.id, "email", email)
    except IntegrityError:
        existing = await identity_link_service.get_user_by_identity(db, "email", email)
        if existing is None:
            raise
        logger.info("magic_link verify: race resolved, reusing existing user_id=%d", existing.id)
        return existing, False

    await log_event(
        db,
        "user.registered.via_magic_link",
        user_id=new_user.id,
        ip=ip,
        user_agent=user_agent,
        details={"identity_kind": "email", "value_masked": _mask_email(email)},
    )
    logger.info("user.registered.via_magic_link user_id=%d", new_user.id)
    return new_user, True
