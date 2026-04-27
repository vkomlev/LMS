"""Эндпоинт VK ID 2.0 OAuth callback."""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db
from app.core.config import Settings
from app.schemas.auth import AuthTokenResponse, VkCallbackRequest
from app.services.auth import identity_link_service, session_service
from app.services.auth.link_token_service import attribute_guest_session
from app.services.auth.vk_oauth_service import exchange_code, get_vk_user_id
from app.services.audit_service import log_event
from app.services.fernet_service import encrypt_token
from app.services.rate_limit_service import get_redis, is_rate_limited

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/vk", tags=["auth"])
_settings = Settings()


@router.post("/callback", response_model=AuthTokenResponse)
async def vk_callback(
    body: VkCallbackRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_bare_db),
) -> AuthTokenResponse:
    """Обменять VK authorization_code (PKCE) на сессию."""
    ip = request.client.host if request.client else "unknown"
    redis = get_redis(_settings.redis_url)
    if await is_rate_limited(redis, f"vk_cb:{ip}", max_requests=10, window_seconds=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")

    try:
        token_data = await exchange_code(
            body.code, body.code_verifier, body.device_id, _settings
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e))

    access_token: str = token_data["access_token"]
    refresh_token_vk: str | None = token_data.get("refresh_token")
    expires_in: int = token_data.get("expires_in", 3600)
    vk_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    try:
        vk_user_id = await get_vk_user_id(access_token)
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e))

    user = await identity_link_service.get_user_by_identity(db, "vk", vk_user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "VK-аккаунт не привязан к пользователю")

    enc_access = encrypt_token(access_token, _settings)
    enc_refresh = encrypt_token(refresh_token_vk, _settings) if refresh_token_vk else None
    await identity_link_service.upsert_identity(
        db, user.id, "vk", vk_user_id,
        vk_access_token_enc=enc_access,
        vk_refresh_token_enc=enc_refresh,
        vk_token_expires_at=vk_token_expires_at,
    )

    if body.guest_session_id:
        await attribute_guest_session(db, body.guest_session_id, user.id)

    ua = request.headers.get("user-agent")
    access, refresh, _ = await session_service.create_session(db, user.id, ua)
    await log_event(db, "login_vk_oauth", user_id=user.id, ip=ip)
    await db.commit()

    response.set_cookie(
        "session", access,
        httponly=True, secure=True, samesite="lax", max_age=3600,
    )
    return AuthTokenResponse(access_token=access, refresh_token=refresh)
