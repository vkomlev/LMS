"""Эндпоинты управления сессией (refresh, logout)."""
import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, get_current_user
from app.auth.current_user import CurrentUser
from app.schemas.auth import AuthTokenResponse, MessageResponse, RefreshRequest
from app.services.auth import session_service
from app.services.auth.cookie import (
    clear_refresh_cookie,
    clear_session_cookie,
    set_refresh_cookie,
    set_session_cookie,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/session", tags=["auth"])


@router.post("/refresh", response_model=AuthTokenResponse)
async def refresh(
    response: Response,
    db: AsyncSession = Depends(get_bare_db),
    body: RefreshRequest | None = None,
    refresh_cookie: str | None = Cookie(default=None, alias="refresh"),
) -> AuthTokenResponse:
    """Обменять refresh_token на новую пару access+refresh токенов (tsk-224).

    Источник refresh-токена по приоритету:
      1. httpOnly cookie `refresh` — web-контекст (SPW шлёт bodyless POST с
         `credentials: include`; токен берётся из cookie, выставленной на логине).
      2. Тело запроса `refresh_token` — tg-app/Bearer-контекст (CloudStorage,
         cookie недоступны).
    При успехе ставим НОВУЮ пару cookie (access `session` + `refresh`) —
    ротация refresh-токена (старая сессия отзывается в `refresh_session`).
    """
    incoming_refresh = refresh_cookie or (body.refresh_token if body else None)
    if not incoming_refresh:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "refresh_token отсутствует"
        )
    result = await session_service.refresh_session(db, incoming_refresh)
    if result is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh_token недействителен или истёк")
    access_token, refresh_token, _ = result
    await db.commit()
    set_session_cookie(response, access_token)
    set_refresh_cookie(response, refresh_token)
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
    clear_session_cookie(response)
    clear_refresh_cookie(response)  # tsk-224: чистим обе cookie при выходе
    return MessageResponse(message="Выполнен выход")
