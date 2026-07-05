"""Эндпоинты управления сессией (refresh, logout)."""
import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, get_current_user
from app.auth.current_user import CurrentUser
from app.core.config import Settings
from app.schemas.auth import AuthTokenResponse, MessageResponse, RefreshRequest
from app.services.auth import session_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/session", tags=["auth"])
_settings = Settings()


@router.post("/refresh", response_model=AuthTokenResponse)
async def refresh(
    body: RefreshRequest,
    response: Response,
    db: AsyncSession = Depends(get_bare_db),
) -> AuthTokenResponse:
    """Обменять refresh_token на новую пару access+refresh токенов."""
    result = await session_service.refresh_session(db, body.refresh_token)
    if result is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh_token недействителен или истёк")
    access_token, refresh_token, _ = result
    await db.commit()
    # Y-5.2: max_age 24ч (см. session_service _ACCESS_TTL_HOURS=24).
    response.set_cookie(
        "session", access_token,
        httponly=True, secure=True, samesite="lax", max_age=86400,
        domain=_settings.cookie_domain,
    )
    return AuthTokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    response: Response,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
    session_cookie: str | None = Cookie(default=None, alias="session"),
) -> MessageResponse:
    """Отозвать текущую сессию и удалить cookie."""
    if session_cookie:
        session_obj = await session_service.validate_session(db, session_cookie)
        if session_obj:
            await session_service.revoke_session(db, session_obj.id)
            await db.commit()
    response.delete_cookie("session", domain=_settings.cookie_domain)
    return MessageResponse(message="Выполнен выход")
