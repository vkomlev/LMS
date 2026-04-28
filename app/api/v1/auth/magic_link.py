"""Эндпоинты email magic-link аутентификации."""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db
from app.core.config import Settings
from app.schemas.auth import AuthTokenResponse, MagicLinkRequest, MagicLinkVerifyRequest, MessageResponse
from app.services.auth import magic_link_service, session_service
from app.services.auth.link_token_service import attribute_guest_session
from app.services.audit_service import log_event
from app.services.rate_limit_service import get_redis, is_rate_limited

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/magic-link", tags=["auth"])
_settings = Settings()


@router.post("/send", response_model=MessageResponse, status_code=status.HTTP_202_ACCEPTED)
async def send_magic_link(
    body: MagicLinkRequest,
    request: Request,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_bare_db),
) -> MessageResponse:
    """Отправить magic-link на указанный email (rate limit: 5 за 10 мин)."""
    ip = request.client.host if request.client else "unknown"
    redis = get_redis(_settings.redis_url)
    if await is_rate_limited(redis, f"ml_send:{ip}", max_requests=5, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")

    email = body.email.lower()
    token = await magic_link_service.create_magic_link(db, email)
    await db.commit()

    background.add_task(magic_link_service.send_magic_link_email, token, email, _settings)
    await log_event(db, "magic_link_sent", ip=ip, details={"email": email})
    await db.commit()

    return MessageResponse(message="Письмо отправлено")


@router.post("/verify", response_model=AuthTokenResponse)
async def verify_magic_link(
    body: MagicLinkVerifyRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_bare_db),
) -> AuthTokenResponse:
    """Верифицировать magic-link токен и выдать сессию."""
    ip = request.client.host if request.client else "unknown"

    link = await magic_link_service.consume_magic_link(db, body.token)
    if link is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Токен недействителен или истёк")

    email = link.email
    ua = request.headers.get("user-agent")

    user, created = await magic_link_service.get_or_create_user_by_email(
        db, email, ip=ip, user_agent=ua,
    )

    if body.guest_session_id:
        await attribute_guest_session(db, body.guest_session_id, user.id)

    access_token, refresh_token, _ = await session_service.create_session(db, user.id, ua)
    await log_event(db, "login_magic_link", user_id=user.id, ip=ip)
    await db.commit()

    response.set_cookie(
        "session", access_token,
        httponly=True, secure=True, samesite="lax", max_age=3600,
    )
    return AuthTokenResponse(access_token=access_token, refresh_token=refresh_token)
