"""Эндпоинты email magic-link аутентификации."""
import logging
from typing import Union

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db
from app.core.config import Settings
from app.schemas.auth import (
    AuthTokenResponse,
    MagicLinkRequest,
    MagicLinkVerifyLinkModeResponse,
    MagicLinkVerifyRequest,
    MessageResponse,
)
from app.services import user_block_service
from app.services.auth import magic_link_service, session_service
from app.services.auth.cookie import set_refresh_cookie, set_session_cookie
from app.services.auth.exceptions import IdentityConflictError
from app.services.auth.guest_attribution_service import attribute_guest_session
from app.services.auth.role_assign_service import ensure_student_access_request
from app.services.audit_service import log_event
from app.services.rate_limit_service import get_redis, is_rate_limited
from app.services.user_merge_service import check_and_merge_duplicate_on_registration

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
    # tsk-755: известен ли адрес — решается ДО создания ссылки. Ответ человеку от
    # этого не зависит (иначе форма входа стала бы способом узнать перебором, кто
    # у нас учится), но оператор должен видеть попытки на ничьи адреса: за неделю
    # до задачи три ученика ждали письма, которое некому было получить.
    recipient_known = await magic_link_service.is_known_recipient(db, email)
    token = await magic_link_service.create_magic_link(db, email)
    await db.commit()

    background.add_task(
        magic_link_service.send_magic_link_email,
        token, email, _settings,
        link_mode=body.link_mode,
    )
    await log_event(
        db,
        "magic_link_sent",
        ip=ip,
        details={
            "email": email,
            "link_mode": body.link_mode,
            "recipient_known": recipient_known,
        },
    )
    await db.commit()
    if not recipient_known:
        # Строка стабильна и рассчитана на поиск по логам прода.
        logger.info(
            "auth.magic_link unknown_recipient email=%s ip=%s",
            magic_link_service.mask_email(email), ip,
        )

    return MessageResponse(message="Письмо отправлено")


@router.post(
    "/verify",
    response_model=Union[AuthTokenResponse, MagicLinkVerifyLinkModeResponse],
)
async def verify_magic_link(
    body: MagicLinkVerifyRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_bare_db),
) -> AuthTokenResponse | MagicLinkVerifyLinkModeResponse:
    """Верифицировать magic-link токен.

    Дефолтный режим (`link_mode=False`): consume + auto-create + выдать сессию.
    Phase Y-3 link-режим (`link_mode=True`): только подтвердить владение email,
    вернуть `magic_link_token` (тот же raw token) для последующего consume в
    /me/identity/email/link. НЕ создаёт user (если email неизвестен → 401), НЕ
    создаёт session, НЕ помечает magic_link.consumed_at — consume произойдёт
    в /me/identity/email/link.
    """
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent")

    if body.link_mode:
        # Phase Y-3: validate-only без consume и без сессии
        link = await magic_link_service.peek_magic_link(db, body.token)
        if link is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Токен недействителен или истёк"
            )
        await log_event(
            db,
            "auth.magic_link.verified_link_mode",
            ip=ip,
            details={"email": link.email},
        )
        await db.commit()
        return MagicLinkVerifyLinkModeResponse(magic_link_token=body.token)

    # Дефолтный flow Y-1.5: consume + create user + session
    link = await magic_link_service.consume_magic_link(db, body.token)
    if link is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Токен недействителен или истёк")

    email = link.email

    try:
        user, created = await magic_link_service.get_or_create_user_by_email(
            db, email, ip=ip, user_agent=ua,
        )
    except IdentityConflictError as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "identity_conflict",
                "conflict_kind": e.conflict_kind,
                "existing_identity_kinds": e.existing_kinds,
                "message": (
                    "Email уже привязан к другому аккаунту в нестандартном состоянии. "
                    "Обратитесь к администратору."
                ),
            },
        )

    # tsk-432: заблокированному отказываем ДО создания сеанса — иначе он
    # «вошёл бы» и упёрся в отказ на первом же экране, не понимая причины.
    await user_block_service.assert_not_blocked(db, user.id)

    if body.guest_session_id:
        await attribute_guest_session(db, body.guest_session_id, user.id)

    access_token, refresh_token, _ = await session_service.create_session(db, user.id, ua)
    await log_event(db, "login_magic_link", user_id=user.id, ip=ip)
    # tsk-172: role-holder без student-роли → заявка на student в очередь
    # одобрения админ-бота. Soft-fail: сбой не должен блокировать вход.
    try:
        await ensure_student_access_request(db, user.id, channel="magic_link")
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

    set_session_cookie(response, access_token)
    set_refresh_cookie(response, refresh_token)  # tsk-224: web-refresh переживает истечение access
    return AuthTokenResponse(access_token=access_token, refresh_token=refresh_token)
