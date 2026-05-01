from fastapi import Cookie, Depends, Header, HTTPException, Query, Security, status
from fastapi.security.api_key import APIKeyHeader, APIKeyQuery
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.auth.current_user import CurrentUser
from app.auth.service_api_key import is_valid_service_key
from app.core.config import Settings
from app.db.session import get_async_db
from app.services.auth import session_service

settings = Settings()

api_key_query = APIKeyQuery(name="api_key", auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


# ---------------------------------------------------------------------------
# Исходная dependency для legacy CRUD эндпоинтов (TG_LMS ботов).
# Не трогаем — backward compat.
# ---------------------------------------------------------------------------

async def get_api_key(
    key: str | None = Security(api_key_query),
) -> str:
    """Проверка api_key в query-параметрах (legacy)."""
    if not key or key not in settings.valid_api_keys:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid or missing API Key")
    return key


async def get_db(
    db: AsyncSession = Depends(get_async_db),
    api_key: str = Depends(get_api_key),
) -> AsyncSession:
    """Legacy dependency: DB + service API key для TG_LMS ботов."""
    return db


# ---------------------------------------------------------------------------
# Новые dependency для SPW эндпоинтов
# ---------------------------------------------------------------------------

async def get_bare_db(db: AsyncSession = Depends(get_async_db)) -> AsyncSession:
    """DB без проверки auth — только для /auth/* эндпоинтов."""
    return db


async def get_current_user(
    db: AsyncSession = Depends(get_async_db),
    # 1. Cookie (SPW браузер)
    session_token: str | None = Cookie(default=None, alias="session"),
    # 2. Bearer header (мобильные / fetch с Authorization)
    authorization: str | None = Header(default=None),
    # 3. URL query token (embed API / email verify redirect)
    token: str | None = Query(default=None),
    # 4. X-API-Key header (service-to-service)
    x_api_key: str | None = Security(api_key_header),
    # 5. Legacy api_key query param (TG_LMS)
    api_key: str | None = Security(api_key_query),
) -> CurrentUser:
    """
    Разрешает CurrentUser из нескольких источников:
    cookie → Bearer → URL token → X-API-Key → legacy api_key.
    Кидает 401 если ни один не прошёл.
    """
    # Bearer token
    bearer: str | None = None
    if authorization and authorization.startswith("Bearer "):
        bearer = authorization.removeprefix("Bearer ").strip()

    for raw_token in [session_token, bearer, token]:
        if raw_token:
            session_obj = await session_service.validate_session(db, raw_token)
            if session_obj:
                from app.models.users import Users  # noqa: PLC0415 — избегаем circular import
                result = await db.execute(
                    select(Users).where(Users.id == session_obj.user_id)
                )
                user = result.scalar_one_or_none()
                if user:
                    # Y-4 pre-S5: defensive self-heal — если у legacy-юзера нет
                    # ни одной роли, тихо назначаем 'student' + audit. Soft-fail:
                    # любой сбой helper'а или commit'а не должен валить auth.
                    await _self_heal_student_role(db, user.id)
                    return CurrentUser(
                        id=user.id,
                        is_service=False,
                        tg_id=str(user.tg_id) if user.tg_id else None,
                        email=user.email,
                    )

    # Service key (X-API-Key header или legacy ?api_key=)
    for svc_key in [x_api_key, api_key]:
        if is_valid_service_key(svc_key):
            return CurrentUser(id=0, is_service=True)

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")


async def require_authenticated(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Требует реального пользователя (не сервисный токен)."""
    if current_user.is_service:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Service token not allowed here")
    return current_user


async def _self_heal_student_role(db: AsyncSession, user_id: int) -> None:
    """Y-4 pre-S5 defensive self-heal: legacy-user без роли получает 'student'.

    Никогда не raises — на любую ошибку (DB conflict, audit-сбой,
    transaction state) логируем warning и продолжаем. Цель —
    не блокировать auth-pipeline на legacy-пробелах.

    Soft-fail rationale: outer transaction зависит от структуры handler'а;
    отдельный commit может конфликтовать. Если падает — assign отложится
    до следующего auth-вызова или вручную через M10 rerun.
    """
    import logging  # noqa: PLC0415 — избегаем top-level импорт для деда
    log = logging.getLogger(__name__)
    try:
        from app.services.auth.role_assign_service import ensure_student_role  # noqa: PLC0415
        assigned = await ensure_student_role(
            db, user_id,
            channel="get_current_user_defensive",
            origin="defensive_self_heal",
        )
        if assigned:
            # Отдельный commit, чтобы зафиксировать assign + audit_event.
            # Если outer transaction уже активен и conflict'ит — except поглотит.
            await db.commit()
    except Exception:
        log.warning(
            "Y-4 pre-S5 self-heal failed для user_id=%s — soft-fail, auth продолжается",
            user_id,
            exc_info=True,
        )
        try:
            await db.rollback()
        except Exception:
            pass
