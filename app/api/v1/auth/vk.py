"""Эндпоинт VK ID 2.0 OAuth callback."""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db
from app.core.config import Settings
from app.schemas.auth import AuthTokenResponse, VkCallbackRequest
from app.services.auth import session_service
from app.services.auth.exceptions import IdentityConflictError
from app.services.auth.guest_attribution_service import attribute_guest_session
from app.services.auth.vk_oauth_service import (
    exchange_code,
    fetch_vk_userinfo,
    get_or_create_user_by_vk,
)
from app.services.auth.cookie import set_session_cookie
from app.services.audit_service import log_event
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
        userinfo = await fetch_vk_userinfo(access_token)
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e))

    ua = request.headers.get("user-agent")

    try:
        user, created = await get_or_create_user_by_vk(
            db,
            vk_user_id=userinfo["user_id"],
            email=userinfo["email"],
            full_name=userinfo["full_name"],
            access_token=access_token,
            refresh_token=refresh_token_vk,
            expires_at=vk_token_expires_at,
            settings=_settings,
            ip=ip,
            user_agent=ua,
        )
    except IdentityConflictError as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "identity_conflict",
                "conflict_kind": e.conflict_kind,
                "existing_identity_kinds": e.existing_kinds,
                "message": (
                    "Этот email уже привязан к другому аккаунту. "
                    "Войдите через email и привяжите VK в /me."
                ),
            },
        )

    if body.guest_session_id:
        await attribute_guest_session(db, body.guest_session_id, user.id)

    access, refresh, _ = await session_service.create_session(db, user.id, ua)
    await log_event(db, "login_vk_oauth", user_id=user.id, ip=ip)
    await db.commit()

    set_session_cookie(response, access)
    return AuthTokenResponse(access_token=access, refresh_token=refresh)
