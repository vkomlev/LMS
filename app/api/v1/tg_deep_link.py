"""Привязка Telegram по глубокой ссылке на бота (tsk-1253).

Два конца одной операции:
- `POST /me/identity/tg/deep-link` — ученик в кабинете получает ссылку на бота;
- `POST /integrations/student-bot/tg-link` — бот (сервисный ключ) гасит токен
  из `/start` и привязывает свой `tg_id` к владельцу токена.

Логика — в `app.services.auth.tg_deep_link_service`.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_authenticated
from app.api.v1.integrations_leads import _service_only
from app.auth.current_user import CurrentUser
from app.services.auth import link_token_service, tg_deep_link_service
from app.services.auth.exceptions import IdentityConflictError

logger = logging.getLogger(__name__)

me_router = APIRouter(prefix="/me/identity/tg", tags=["me"])
bot_router = APIRouter(prefix="/integrations/student-bot", tags=["student-bot"])


class TgDeepLinkResponse(BaseModel):
    """Ссылка на бота с одноразовым токеном."""

    url: str = Field(description="https://t.me/<бот>?start=tg_<токен>")
    expires_at: datetime


class TgBotLinkRequest(BaseModel):
    """Что бот присылает после /start tg_<токен>."""

    token: str = Field(min_length=10, max_length=128)
    tg_id: int = Field(gt=0)


class TgBotLinkResponse(BaseModel):
    """Итог привязки."""

    user_id: int


class TgBotAvailability(BaseModel):
    """Есть ли у школы ученический бот (нет — кнопку не показываем)."""

    available: bool


@me_router.get("/bot", response_model=TgBotAvailability)
async def tg_bot_availability(
    _: CurrentUser = Depends(require_authenticated),
) -> TgBotAvailability:
    """Показывать ли ученику кнопку «Подключить Telegram»."""
    return TgBotAvailability(available=bool(tg_deep_link_service.bot_username()))


@me_router.post("/deep-link", response_model=TgDeepLinkResponse)
async def issue_tg_deep_link(
    current_user: CurrentUser = Depends(require_authenticated),
) -> TgDeepLinkResponse:
    """Выдать ученику ссылку на бота для привязки Telegram (действует 5 минут)."""
    try:
        url, expires_at = await tg_deep_link_service.issue_deep_link(current_user.id)
    except tg_deep_link_service.BotNotConfiguredError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Telegram-бот у школы не подключён")
    except link_token_service.LinkTokenServiceUnavailableError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Сервис привязки временно недоступен. Попробуйте через минуту.",
        )
    return TgDeepLinkResponse(url=url, expires_at=expires_at)


@bot_router.post("/tg-link", response_model=TgBotLinkResponse)
async def bot_tg_link(
    body: TgBotLinkRequest,
    _: CurrentUser = Depends(_service_only),
    db: AsyncSession = Depends(get_async_db),
) -> TgBotLinkResponse:
    """Погасить токен из /start и привязать Telegram. 404 — ссылка устарела, 409 — чужой TG."""
    try:
        user_id = await tg_deep_link_service.link_from_bot(
            db, raw_token=body.token, tg_id=body.tg_id,
        )
    except link_token_service.LinkTokenError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ссылка устарела или уже использована")
    except link_token_service.LinkTokenServiceUnavailableError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Хранилище токенов недоступно")
    except IdentityConflictError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail={
            "error": "identity_conflict", "conflict_kind": e.conflict_kind,
        })
    return TgBotLinkResponse(user_id=user_id)
