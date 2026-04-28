"""VK ID 2.0 OAuth flow: обмен code → tokens, извлечение user_id.

Phase Y-1.5: добавлено auto-create user (см. ADR-0021) с защитой
от identity-takeover через 409 при VK userinfo.email overlap с
существующим email-only user. Race-safety: INSERT в SAVEPOINT
(begin_nested) — IntegrityError на UNIQUE(kind,value) откатывает
только savepoint, основная транзакция (с обменом VK token и атрибуцией
guest_session) продолжается.
"""
import logging
from datetime import datetime
from typing import Iterable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.core.config import Settings
from app.models.users import Users
from app.services.audit_service import log_event
from app.services.auth import identity_link_service
from app.services.fernet_service import encrypt_token

logger = logging.getLogger(__name__)

_VK_TOKEN_URL = "https://id.vk.com/oauth2/auth"
_VK_USERINFO_URL = "https://id.vk.com/oauth2/user_info"


class IdentityConflictError(Exception):
    """VK userinfo.email уже привязан к другому пользователю.

    Auto-merge запрещён ADR-0021 (защита от identity-takeover через
    подделанный VK userinfo email). Linking — только через explicit
    /me/identity/.../link с link_token (Y-3).
    """

    def __init__(self, conflict_kind: str, existing_kinds: Iterable[str]) -> None:
        self.conflict_kind = conflict_kind
        self.existing_kinds = list(existing_kinds)
        super().__init__(f"identity_conflict: {conflict_kind}")


async def exchange_code(
    code: str,
    code_verifier: str,
    device_id: str,
    settings: Settings,
) -> dict:
    """
    Обменять authorization_code (PKCE) на access+refresh токены VK ID 2.0.
    Возвращает dict с access_token, refresh_token, expires_in, user_id.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _VK_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "client_id": settings.vk_id_client_id,
                "device_id": device_id,
                "redirect_uri": settings.vk_id_redirect_uri,
            },
        )
    if resp.status_code != 200:
        logger.error("VK token exchange error %s: %s", resp.status_code, resp.text)
        raise ValueError("VK token exchange failed")

    data = resp.json()
    if "error" in data:
        raise ValueError(f"VK error: {data['error']}")

    return data


async def get_vk_user_id(access_token: str) -> str:
    """Получить VK user_id через /user_info."""
    info = await fetch_vk_userinfo(access_token)
    return info["user_id"]


async def fetch_vk_userinfo(access_token: str) -> dict:
    """Получить VK userinfo: user_id, email (опц.), full_name (опц.).

    Возвращает dict с ключами user_id (str, обязательно), email (str | None),
    full_name (str | None). Email присутствует только если scope включил email.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _VK_USERINFO_URL,
            data={"access_token": access_token},
        )
    if resp.status_code != 200:
        raise ValueError("VK userinfo failed")
    data = resp.json()
    user = data.get("user", {})
    uid = user.get("user_id") or user.get("id")
    if not uid:
        raise ValueError("VK user_id not found in userinfo response")

    email = user.get("email")
    if email:
        email = email.strip().lower() or None

    first = (user.get("first_name") or "").strip()
    last = (user.get("last_name") or "").strip()
    full_name = (first + " " + last).strip() or None

    return {"user_id": str(uid), "email": email, "full_name": full_name}


async def get_or_create_user_by_vk(
    db: AsyncSession,
    vk_user_id: str,
    email: str | None,
    full_name: str | None,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime | None,
    settings: Settings,
    ip: str | None,
    user_agent: str | None,
) -> tuple[Users, bool]:
    """Найти пользователя по vk-identity или создать нового атомарно.

    Если найден — обновляет VK token поля (ротация при каждом login).
    Если не найден и email указан — проверяет на overlap c email-only user;
    при overlap кидает IdentityConflictError (auto-merge запрещён ADR-0021).
    Возвращает (user, created_flag).
    """
    enc_access = encrypt_token(access_token, settings)
    enc_refresh = encrypt_token(refresh_token, settings) if refresh_token else None

    user = await identity_link_service.get_user_by_identity(db, "vk", vk_user_id)
    if user is not None:
        await identity_link_service.upsert_identity(
            db, user.id, "vk", vk_user_id,
            vk_access_token_enc=enc_access,
            vk_refresh_token_enc=enc_refresh,
            vk_token_expires_at=expires_at,
        )
        return user, False

    if email:
        existing_email_user = await identity_link_service.get_user_by_identity(
            db, "email", email
        )
        if existing_email_user is not None:
            raise IdentityConflictError(
                conflict_kind="email_already_linked",
                existing_kinds=["email"],
            )

    new_user = Users(
        email=email, password_hash=None, full_name=full_name, tg_id=None,
    )
    try:
        async with db.begin_nested():
            db.add(new_user)
            await db.flush()
            await identity_link_service.upsert_identity(
                db, new_user.id, "vk", vk_user_id,
                vk_access_token_enc=enc_access,
                vk_refresh_token_enc=enc_refresh,
                vk_token_expires_at=expires_at,
            )
            if email:
                await identity_link_service.upsert_identity(db, new_user.id, "email", email)
    except IntegrityError:
        existing = await identity_link_service.get_user_by_identity(db, "vk", vk_user_id)
        if existing is None:
            raise
        await identity_link_service.upsert_identity(
            db, existing.id, "vk", vk_user_id,
            vk_access_token_enc=enc_access,
            vk_refresh_token_enc=enc_refresh,
            vk_token_expires_at=expires_at,
        )
        logger.info("vk_callback: race resolved, reusing existing user_id=%d", existing.id)
        return existing, False

    await log_event(
        db,
        "user.registered.via_vk",
        user_id=new_user.id,
        ip=ip,
        user_agent=user_agent,
        details={
            "identity_kind": "vk",
            "value_masked": vk_user_id,
            "email_provided": bool(email),
        },
    )
    logger.info("user.registered.via_vk user_id=%d email_provided=%s", new_user.id, bool(email))
    return new_user, True
