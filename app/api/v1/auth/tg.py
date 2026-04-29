"""Эндпоинт аутентификации через Telegram WebApp initData."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db
from app.core.config import Settings
from app.schemas.auth import AuthTokenResponse, TgInitRequest
from app.services.auth import session_service
from app.services.auth.guest_attribution_service import attribute_guest_session
from app.services.auth.tg_init_service import (
    extract_tg_full_name,
    extract_tg_user_id,
    get_or_create_user_by_tg,
    verify_tg_init_data,
)
from app.services.audit_service import log_event
from app.services.rate_limit_service import get_redis, is_rate_limited

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/tg", tags=["auth"])
_settings = Settings()


@router.post("/init", response_model=AuthTokenResponse)
async def tg_init(
    body: TgInitRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_bare_db),
) -> AuthTokenResponse:
    """Авторизация через Telegram WebApp initData HMAC."""
    ip = request.client.host if request.client else "unknown"
    redis = get_redis(_settings.redis_url)
    if await is_rate_limited(redis, f"tg_init:{ip}", max_requests=20, window_seconds=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")

    if not _settings.tg_bot_token_for_initdata:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "TG auth не настроен")

    params = verify_tg_init_data(body.init_data, _settings.tg_bot_token_for_initdata)
    if params is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверная подпись initData")

    tg_id_str = extract_tg_user_id(params)
    if not tg_id_str:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "tg_id не найден в initData")

    try:
        tg_id_int = int(tg_id_str)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "tg_id некорректного формата")

    full_name = extract_tg_full_name(params)
    ua = request.headers.get("user-agent")

    user, created = await get_or_create_user_by_tg(
        db, tg_id_int, full_name, ip=ip, user_agent=ua,
    )

    if body.guest_session_id:
        await attribute_guest_session(db, body.guest_session_id, user.id)

    access_token, refresh_token, _ = await session_service.create_session(db, user.id, ua)
    await log_event(db, "login_tg_initdata", user_id=user.id, ip=ip)
    await db.commit()

    response.set_cookie(
        "session", access_token,
        httponly=True, secure=True, samesite="lax", max_age=3600,
    )
    return AuthTokenResponse(access_token=access_token, refresh_token=refresh_token)
