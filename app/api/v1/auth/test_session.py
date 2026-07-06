"""Тестовый auth-эндпоинт для bootstrap cookie сессий в E2E spec'ах
(Phase Y-4 pre-S5).

POST /api/v1/auth/test/issue-session
- Двойной gating: `settings.env in {"dev","test"}` AND
  `settings.test_endpoints_enabled=True` — иначе 404 (path-as-disabled,
  без обработки body — fail-fast).
- Auth: X-API-Key (header) с constant-time compare против `valid_api_keys`.
- Body: `{user_id: int}` — реальный user из `users` table.
- Side-effect: defensive self-heal student role (если нет ролей) +
  `session_service.create_session(user_id, ttl=3600)` + audit
  `auth.test.session_issued` + Set-Cookie `lms_session=...; HttpOnly;
  SameSite=Lax; Max-Age=3600`.

Назначение: SPW Playwright live-spec (Y-4 S5) bootstrap'ит cookie без
прохождения через email magic-link. Никогда не должен попасть в prod.
"""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db
from app.core.config import Settings
from app.models.users import Users
from app.schemas.auth_test import TestIssueSessionRequest, TestIssueSessionResponse
from app.services import audit_service
from app.services.auth import session_service
from app.services.auth.cookie import set_session_cookie
from app.services.auth.role_assign_service import ensure_student_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/test", tags=["auth-test"])
_settings = Settings()

_TEST_SESSION_TTL_SECONDS = 3600  # Q5.2: TTL=1 час, blast-radius ограничен


def _validate_service_key_constant_time(provided: str | None) -> bool:
    """Constant-time compare: провайденный ключ vs ВСЕ valid_api_keys."""
    if not provided:
        return False
    found = False
    for valid in _settings.valid_api_keys:
        if secrets.compare_digest(provided, valid):
            found = True
            # NB: не break — выполняем все compare_digest для constant-time
    return found


@router.post(
    "/issue-session",
    response_model=TestIssueSessionResponse,
    status_code=status.HTTP_200_OK,
    summary="Выдать cookie-сессию для тестового пользователя (только dev/test)",
    responses={
        200: {"description": "Cookie выдан, сессия создана"},
        401: {"description": "Invalid X-API-Key"},
        403: {"description": "Target user is service"},
        404: {"description": "Endpoint disabled OR user not found"},
    },
)
async def issue_test_session(
    body: TestIssueSessionRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> TestIssueSessionResponse:
    """Выдать cookie-сессию для тестового пользователя.

    Двойной gating защищает от случайного включения в prod даже при
    `TEST_ENDPOINTS_ENABLED=true`: если `ENV=production`, endpoint
    возвращает 404 ДО обработки body (`raise HTTPException(404)` без detail).
    """
    # Q4=C: двойной gating, fail-fast 404 без подробностей.
    if (
        _settings.env not in ("dev", "test")
        or not _settings.test_endpoints_enabled
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    # Constant-time compare X-API-Key.
    if not _validate_service_key_constant_time(x_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid X-API-Key")

    # Проверка target user.
    result = await db.execute(select(Users).where(Users.id == body.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    ip = request.client.host if request.client else None

    # Defensive self-heal student role (если у user нет ролей).
    try:
        await ensure_student_role(
            db, user.id,
            channel="auth_test_session", origin="test_session_issue",
        )
    except Exception:
        logger.warning(
            "Y-4 pre-S5 test session: ensure_student_role failed user_id=%s — soft-fail",
            user.id, exc_info=True,
        )

    # Создаём сессию через session_service. Сигнатура (db, user_id, ua_fingerprint).
    # Возвращает (access_token_hex, refresh_token_hex, UserSession ORM row).
    access_token, _refresh_token, session = await session_service.create_session(
        db, user_id=user.id, ua_fingerprint=request.headers.get("user-agent"),
    )

    # Override expires_at до TTL=3600 (по умолчанию service создаёт на 15 мин).
    # Делаем явно через UPDATE с расчётом от сейчас.
    from datetime import datetime, timezone  # noqa: PLC0415
    custom_expires = datetime.now(timezone.utc) + timedelta(seconds=_TEST_SESSION_TTL_SECONDS)
    session.expires_at = custom_expires
    await db.flush()

    # Audit БЕЗ значения cookie / API-key — только метаданные.
    await audit_service.log_event(
        db,
        audit_service.AUTH_TEST_SESSION_ISSUED,
        user_id=user.id,
        ip=ip,
        details={
            "session_id": str(session.id),
            "ttl_seconds": _TEST_SESSION_TTL_SECONDS,
            "channel": "test_endpoint",
        },
    )
    await db.commit()

    # Set-Cookie: имя 'session' (тот же alias, что использует get_current_user).
    set_session_cookie(
        response, access_token,
        max_age=_TEST_SESSION_TTL_SECONDS,
        secure=_settings.cookie_secure,
    )

    logger.info(
        "Y-4 pre-S5: test session issued user_id=%s ttl=%ds (ip=%s)",
        user.id, _TEST_SESSION_TTL_SECONDS, ip,
    )

    return TestIssueSessionResponse(
        user_id=user.id,
        expires_at=custom_expires,
    )
