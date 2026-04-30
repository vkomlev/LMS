"""Эндпоинты /me — профиль, identities, прогресс, last-position, streak,
история (Phase Y-1 + Y-3 + Y-4).

Phase Y-3 добавляет:
- GET  /me/identities         — список identity_link с masked values
- GET  /me/courses            — активные курсы + progress (single roundtrip CTE)
- GET  /me/last-position      — последняя активность + резолв next-item
- GET  /me/streak             — streak дней подряд в Europe/Moscow
- POST /me/identity/{kind}/link — привязка новой identity к current user

Phase Y-4 добавляет:
- GET  /me/history            — список последних попыток + фильтры

См. tech-spec Y-3 §5.1-5.4, §5.6, §7.6, §7.7;
    tech-spec Y-4 (LMS-side backend) §4.2.5.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_authenticated
from app.auth.current_user import CurrentUser
from app.core.config import Settings
from app.schemas.auth import (
    IdentityLinkEmailRequest,
    IdentityLinkResponse,
    IdentityLinkTgRequest,
    IdentityLinkVkRequest,
    IdentityLinkedItem,
)
from app.schemas.me import (
    CourseProgress,
    CourseWithProgressRead,
    HistoryItem,
    IdentityRead,
    LastPositionRead,
    MeResponse,
    StreakRead,
)
from app.services import me_service
from app.services.audit_service import log_event
from app.services.auth import (
    identity_link_service,
    link_token_service,
    magic_link_service,
    tg_init_service,
    vk_oauth_service,
)
from app.services.auth.exceptions import IdentityConflictError
from app.services.fernet_service import encrypt_token
from app.services.rate_limit_service import get_redis, is_rate_limited

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/me", tags=["me"])
_settings = Settings()


# ── GET /me ─────────────────────────────────────────────────────────────────

@router.get("", response_model=MeResponse)
async def get_me(
    current_user: CurrentUser = Depends(require_authenticated),
) -> MeResponse:
    """Вернуть профиль аутентифицированного пользователя."""
    return MeResponse(
        id=current_user.id,
        email=current_user.email,
        tg_id=current_user.tg_id,
        is_service=current_user.is_service,
    )


# ── GET /me/identities ──────────────────────────────────────────────────────

@router.get("/identities", response_model=list[IdentityRead])
async def list_identities(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> list[IdentityRead]:
    """Список identity_link текущего пользователя с masked values (см. §5.1)."""
    items = await me_service.get_identities(db, current_user.id)
    return [IdentityRead(**item) for item in items]


# ── GET /me/courses ─────────────────────────────────────────────────────────

@router.get("/courses", response_model=list[CourseWithProgressRead])
async def list_courses(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> list[CourseWithProgressRead]:
    """Активные курсы пользователя + progress (см. §5.2). Single SQL roundtrip."""
    items = await me_service.get_courses_with_progress(db, current_user.id)
    return [
        CourseWithProgressRead(
            course_id=it["course_id"],
            course_uid=it["course_uid"],
            title=it["title"],
            order_number=it["order_number"],
            progress=CourseProgress(**it["progress"]),
            last_active_at=it["last_active_at"],
            is_completed=it["is_completed"],
        )
        for it in items
    ]


# ── GET /me/last-position ───────────────────────────────────────────────────

@router.get("/last-position", response_model=LastPositionRead | None)
async def get_last_position(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> LastPositionRead | None:
    """Последняя активность пользователя + next-item resolve (см. §5.3)."""
    pos = await me_service.get_last_position(db, current_user.id)
    if pos is None:
        return None
    return LastPositionRead(**pos)


# ── GET /me/streak ──────────────────────────────────────────────────────────

@router.get("/streak", response_model=StreakRead)
async def get_streak(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> StreakRead:
    """Streak дней подряд в Europe/Moscow (см. §5.4)."""
    s = await me_service.get_streak(db, current_user.id)
    return StreakRead(**s)


# ── GET /me/history (Phase Y-4) ─────────────────────────────────────────────

@router.get("/history", response_model=list[HistoryItem])
async def get_history(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
    limit: int = Query(50, ge=1, le=200, description="Лимит (max 200)"),
    offset: int = Query(0, ge=0, description="Смещение"),
    filter_: Literal["all", "pending_review", "passed", "failed"] = Query(
        "all", alias="filter", description="Фильтр статусу"
    ),
) -> list[HistoryItem]:
    """История попыток ученика с фильтрами (Phase Y-4 backend §4.2.5)."""
    rows = await me_service.get_history(
        db, current_user.id, filter_=filter_, limit=limit, offset=offset
    )
    return [HistoryItem(**row) for row in rows]


# ── POST /me/identity/{kind}/link ───────────────────────────────────────────

def _conflict_to_http(e: IdentityConflictError) -> HTTPException:
    """Маппинг IdentityConflictError → HTTP 409 c унифицированным body."""
    return HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "error": "identity_conflict",
            "conflict_kind": e.conflict_kind,
            "existing_identity_kinds": e.existing_kinds,
            "message": (
                "Эта identity уже привязана к другому аккаунту. "
                "Войдите через ту identity, чтобы управлять привязкой."
            ),
        },
    )


def _link_token_invalid_http() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "link_token недействителен, истёк или уже использован",
    )


async def _enforce_link_identity_rate_limit(current_user_id: int) -> None:
    """Rate-limit 30/мин per user на /me/identity/{kind}/link (Y-3.2 defence-in-depth).

    Защищает от token-guessing abuse: даже с действительным session-токеном
    атакующий не может бесконечно пробовать чужие link_token. Fail-open при
    недоступности Redis (через rate_limit_service — стандартный паттерн).
    """
    redis = get_redis(_settings.redis_url)
    if await is_rate_limited(
        redis, f"link_identity:user:{current_user_id}", max_requests=30, window_seconds=60
    ):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Слишком много попыток привязки identity. Попробуйте через минуту.",
        )


async def _consume_link_token_for_user(
    db: AsyncSession,
    raw_token: str,
    expected_kind: Literal["email", "tg", "vk"],
    current_user_id: int,
    ip: str,
) -> None:
    """Atomic consume + валидация owner_user/kind.

    Raise HTTPException 401 на любую ошибку (invalid/expired/consumed или mismatch).
    Raise HTTPException 503 если link_token storage недоступен в production.

    На mismatch (wrong user_id / wrong kind) пишет audit_event
    `auth.link_token.consume_mismatch` для forensics (Y-3.1 / techlead S3-7).
    """
    redis = get_redis(_settings.redis_url)
    try:
        payload = await link_token_service.consume(redis, raw_token)
    except link_token_service.LinkTokenError:
        raise _link_token_invalid_http()
    except link_token_service.LinkTokenServiceUnavailableError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Сервис привязки временно недоступен. Попробуйте через минуту.",
        )
    if payload.user_id != current_user_id or payload.kind != expected_kind:
        # Mismatch — токен принадлежит другому user или предназначен для другого kind.
        # Логируем как forensics event и не различаем причину для клиента.
        mismatch_reason = (
            "user_id" if payload.user_id != current_user_id else "kind"
        )
        await log_event(
            db,
            "auth.link_token.consume_mismatch",
            user_id=current_user_id,
            ip=ip,
            details={
                "expected_kind": expected_kind,
                "payload_kind": payload.kind,
                "expected_user_id": current_user_id,
                "payload_user_id": payload.user_id,
                "mismatch_reason": mismatch_reason,
            },
        )
        await db.commit()
        raise _link_token_invalid_http()


@router.post(
    "/identity/email/link",
    response_model=IdentityLinkResponse,
    status_code=status.HTTP_200_OK,
)
async def link_identity_email(
    body: IdentityLinkEmailRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> IdentityLinkResponse:
    """Привязать email-identity к current user. Email подтверждается magic-link consume.

    Body содержит:
    - link_token: одноразовый токен из /auth/link-token/issue {kind:'email'}
    - magic_link_token: raw token, который вернул /auth/magic-link/verify {link_mode:true}
    """
    ip = request.client.host if request.client else "unknown"
    await _enforce_link_identity_rate_limit(current_user.id)
    await _consume_link_token_for_user(db, body.link_token, "email", current_user.id, ip)

    # Consume magic_link атомарно (помечает consumed_at)
    link = await magic_link_service.consume_magic_link(db, body.magic_link_token)
    if link is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "magic_link_token недействителен, истёк или уже использован",
        )
    email = link.email.lower()

    try:
        new_link = await identity_link_service.link_existing_user(
            db, current_user.id, "email", email
        )
    except IdentityConflictError as e:
        await log_event(
            db,
            "auth.identity.linked.conflict",
            user_id=current_user.id,
            ip=ip,
            details={"kind": "email", "conflict_kind": e.conflict_kind},
        )
        await db.commit()
        raise _conflict_to_http(e)

    masked = me_service.mask_value("email", email)
    await log_event(
        db,
        "auth.identity.linked",
        user_id=current_user.id,
        ip=ip,
        details={"kind": "email", "value_masked": masked, "source": "magic_link"},
    )
    await db.commit()
    return IdentityLinkResponse(
        identity=IdentityLinkedItem(
            kind="email", value_masked=masked, created_at=new_link.created_at
        )
    )


@router.post(
    "/identity/tg/link",
    response_model=IdentityLinkResponse,
    status_code=status.HTTP_200_OK,
)
async def link_identity_tg(
    body: IdentityLinkTgRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> IdentityLinkResponse:
    """Привязать Telegram-identity к current user. Подтверждение — initData HMAC."""
    ip = request.client.host if request.client else "unknown"
    await _enforce_link_identity_rate_limit(current_user.id)
    await _consume_link_token_for_user(db, body.link_token, "tg", current_user.id, ip)

    if not _settings.tg_bot_token_for_initdata:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "TG auth не настроен")

    params = tg_init_service.verify_tg_init_data(
        body.init_data, _settings.tg_bot_token_for_initdata
    )
    if params is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверная подпись initData")

    tg_id_str = tg_init_service.extract_tg_user_id(params)
    if not tg_id_str:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "tg_id не найден в initData")

    try:
        new_link = await identity_link_service.link_existing_user(
            db, current_user.id, "tg", tg_id_str
        )
    except IdentityConflictError as e:
        await log_event(
            db,
            "auth.identity.linked.conflict",
            user_id=current_user.id,
            ip=ip,
            details={"kind": "tg", "conflict_kind": e.conflict_kind},
        )
        await db.commit()
        raise _conflict_to_http(e)

    masked = me_service.mask_value("tg", tg_id_str)
    await log_event(
        db,
        "auth.identity.linked",
        user_id=current_user.id,
        ip=ip,
        details={"kind": "tg", "value_masked": masked, "source": "init_data"},
    )
    await db.commit()
    return IdentityLinkResponse(
        identity=IdentityLinkedItem(
            kind="tg", value_masked=masked, created_at=new_link.created_at
        )
    )


@router.post(
    "/identity/vk/link",
    response_model=IdentityLinkResponse,
    status_code=status.HTTP_200_OK,
)
async def link_identity_vk(
    body: IdentityLinkVkRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> IdentityLinkResponse:
    """Привязать VK-identity к current user.

    PKCE flow для existing user: SPW отправил state="link:<token>",
    vk-relay перенаправил браузер на /me/identity/vk/link, body содержит
    уже очищенный link_token (без префикса 'link:').
    """
    ip = request.client.host if request.client else "unknown"
    await _enforce_link_identity_rate_limit(current_user.id)
    await _consume_link_token_for_user(db, body.link_token, "vk", current_user.id, ip)

    try:
        token_data = await vk_oauth_service.exchange_code(
            body.code, body.code_verifier, body.device_id, _settings
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"VK exchange failed: {e}")

    access_token: str = token_data["access_token"]
    refresh_token_vk: str | None = token_data.get("refresh_token")
    expires_in: int = token_data.get("expires_in", 3600)
    vk_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    try:
        userinfo = await vk_oauth_service.fetch_vk_userinfo(access_token)
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"VK userinfo failed: {e}")

    vk_user_id: str = userinfo["user_id"]
    enc_access = encrypt_token(access_token, _settings)
    enc_refresh = encrypt_token(refresh_token_vk, _settings) if refresh_token_vk else None

    try:
        new_link = await identity_link_service.link_existing_user(
            db,
            current_user.id,
            "vk",
            vk_user_id,
            vk_access_token_enc=enc_access,
            vk_refresh_token_enc=enc_refresh,
            vk_token_expires_at=vk_token_expires_at,
        )
    except IdentityConflictError as e:
        await log_event(
            db,
            "auth.identity.linked.conflict",
            user_id=current_user.id,
            ip=ip,
            details={"kind": "vk", "conflict_kind": e.conflict_kind},
        )
        await db.commit()
        raise _conflict_to_http(e)

    masked = me_service.mask_value("vk", vk_user_id)
    await log_event(
        db,
        "auth.identity.linked",
        user_id=current_user.id,
        ip=ip,
        details={"kind": "vk", "value_masked": masked, "source": "vk_pkce"},
    )
    await db.commit()
    return IdentityLinkResponse(
        identity=IdentityLinkedItem(
            kind="vk", value_masked=masked, created_at=new_link.created_at
        )
    )
