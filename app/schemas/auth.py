"""Pydantic схемы для auth эндпоинтов."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


# ── Magic Link ──────────────────────────────────────────────────────────────

class MagicLinkRequest(BaseModel):
    email: EmailStr
    link_mode: bool = False  # Phase Y-3: при True email-linking flow (см. tech-spec §5.6, §7.5)


class MagicLinkVerifyRequest(BaseModel):
    token: str = Field(..., min_length=1)
    guest_session_id: str | None = None
    link_mode: bool = False  # Phase Y-3: при True не создаём user/session, возвращаем magic_link_token


class MagicLinkVerifyLinkModeResponse(BaseModel):
    """Ответ /auth/magic-link/verify в режиме link_mode=True.

    Не выдаёт сессию (session/refresh tokens) — вместо этого возвращает
    magic_link_token (текущий raw token) для последующего consume в
    /me/identity/email/link.
    """

    magic_link_token: str
    email_verified: bool = True


# ── Telegram initData ────────────────────────────────────────────────────────

class TgInitRequest(BaseModel):
    init_data: str = Field(..., min_length=1)
    guest_session_id: str | None = None


# ── VK ID ────────────────────────────────────────────────────────────────────

class VkCallbackRequest(BaseModel):
    code: str = Field(..., min_length=1)
    code_verifier: str = Field(..., min_length=1)
    device_id: str = Field(..., min_length=1)
    guest_session_id: str | None = None


# ── Session / Refresh ────────────────────────────────────────────────────────

class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


# ── Responses ────────────────────────────────────────────────────────────────

class AuthTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class MessageResponse(BaseModel):
    message: str


# ── Phase Y-3: Identity linking ──────────────────────────────────────────────

class LinkTokenIssueRequest(BaseModel):
    """POST /auth/link-token/issue — какой kind identity готовится к привязке."""

    kind: Literal["email", "tg", "vk"]


class LinkTokenIssueResponse(BaseModel):
    link_token: str
    expires_at: datetime


class IdentityLinkEmailRequest(BaseModel):
    """POST /me/identity/email/link body."""

    link_token: str = Field(..., min_length=1)
    magic_link_token: str = Field(..., min_length=1)


class IdentityLinkTgRequest(BaseModel):
    """POST /me/identity/tg/link body."""

    link_token: str = Field(..., min_length=1)
    init_data: str = Field(..., min_length=1)


class IdentityLinkVkRequest(BaseModel):
    """POST /me/identity/vk/link body.

    `link_token` — уже очищенный от префикса `link:` (префикс срезает SPW
    при чтении `state` из адресной строки; backend prefix не ожидает).
    См. CB tech-spec Y-3 §22.1.
    """

    link_token: str = Field(..., min_length=1)
    code: str = Field(..., min_length=1)
    code_verifier: str = Field(..., min_length=1)
    device_id: str = Field(..., min_length=1)


class IdentityLinkedItem(BaseModel):
    """Идентифицирующая часть identity_link для response (без чувствительных полей)."""

    kind: Literal["email", "tg", "vk"]
    value_masked: str
    created_at: datetime


class IdentityLinkResponse(BaseModel):
    """Успешный ответ /me/identity/{kind}/link."""

    ok: bool = True
    identity: IdentityLinkedItem
