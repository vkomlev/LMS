"""Pydantic схемы для auth эндпоинтов."""
from pydantic import BaseModel, EmailStr, Field


# ── Magic Link ──────────────────────────────────────────────────────────────

class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkVerifyRequest(BaseModel):
    token: str = Field(..., min_length=1)
    guest_session_id: str | None = None


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
