"""Эндпоинт VK ID 2.0 OAuth callback."""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db
from app.core.config import Settings
from app.schemas.auth import AuthTokenResponse, VkCallbackRequest
from app.services import user_block_service
from app.services.auth import session_service
from app.services.auth.exceptions import IdentityConflictError
from app.services.auth.guest_attribution_service import attribute_guest_session
from app.services.auth.vk_oauth_service import (
    exchange_code,
    fetch_vk_userinfo,
    get_or_create_user_by_vk,
)
from app.services.auth.cookie import set_refresh_cookie, set_session_cookie
from app.services.auth.role_assign_service import ensure_student_access_request
from app.services.audit_service import log_event
from app.services.rate_limit_service import get_redis, is_rate_limited
from app.services.user_merge_service import check_and_merge_duplicate_on_registration

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
        logger.warning("VK code exchange failed: %s", e)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "code_invalid",
                "message": "Код авторизации VK недействителен или истёк.",
            },
        )

    access_token: str = token_data["access_token"]
    refresh_token_vk: str | None = token_data.get("refresh_token")
    expires_in: int = token_data.get("expires_in", 3600)
    vk_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    try:
        userinfo = await fetch_vk_userinfo(access_token)
    except ValueError as e:
        logger.warning("VK userinfo fetch failed: %s", e)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "id_token_invalid",
                "message": "Не удалось подтвердить личность через VK ID.",
            },
        )

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

    # tsk-432: заблокированному отказываем ДО создания сеанса — иначе он
    # «вошёл бы» и упёрся в отказ на первом же экране, не понимая причины.
    await user_block_service.assert_not_blocked(db, user.id)

    if body.guest_session_id:
        await attribute_guest_session(db, body.guest_session_id, user.id)

    access, refresh, _ = await session_service.create_session(db, user.id, ua)
    await log_event(db, "login_vk_oauth", user_id=user.id, ip=ip)
    # tsk-172: role-holder без student-роли → заявка на student. Soft-fail.
    try:
        await ensure_student_access_request(db, user.id, channel="vk_callback")
    except Exception:
        logger.exception(
            "tsk-172 ensure_student_access_request failed user_id=%s", user.id
        )
    if created:
        # tsk-455: проверка на дубль с "плавающим" аккаунтом сразу при
        # регистрации, не дожидаясь ручного запуска
        # tsk442_auto_merge_duplicates.py. Soft-fail — не должно ломать
        # авторизацию.
        try:
            await check_and_merge_duplicate_on_registration(db, new_user_id=user.id)
        except Exception:
            logger.exception(
                "tsk-455 check_and_merge_duplicate_on_registration failed user_id=%s",
                user.id,
            )
    await db.commit()

    set_session_cookie(response, access)
    set_refresh_cookie(response, refresh)  # tsk-224: web-refresh переживает истечение access
    return AuthTokenResponse(access_token=access, refresh_token=refresh)
