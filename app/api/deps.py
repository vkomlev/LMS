from typing import Awaitable, Callable

from fastapi import Cookie, Depends, Header, HTTPException, Query, Security, status
from fastapi.security.api_key import APIKeyHeader, APIKeyQuery
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.auth.current_user import CurrentUser
from app.auth.service_api_key import is_valid_service_key
from app.core.config import Settings
from app.db.audit_context import set_audit_actor
from app.db.session import get_async_db
from app.services.auth import session_service
from app.services import user_block_service

settings = Settings()

api_key_query = APIKeyQuery(name="api_key", auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


# ---------------------------------------------------------------------------
# Исходная dependency для legacy CRUD эндпоинтов (TG_LMS ботов).
# Не трогаем — backward compat.
# ---------------------------------------------------------------------------

async def get_api_key(
    key_query: str | None = Security(api_key_query),
    key_header: str | None = Security(api_key_header),
) -> str:
    """Проверка сервисного ключа: заголовок `X-API-Key` ИЛИ legacy `?api_key=`.

    tsk-586: до 2026-08-08 эта дверь читала только query-параметр. TG_LMS с
    коммита 8ceed6f (tsk-497) шлёт ключ ТОЛЬКО заголовком — все ~45 эндпоинтов
    на `Depends(get_db)` отвечали ботам 403 («Недостаточно прав»). Заголовок
    проверяется первым: это текущий транспорт клиентов, query оставлен для
    обратной совместимости (ContentBackbone ходит с `?api_key=`).

    :param key_query: ключ из query-параметра `api_key` (legacy-транспорт).
    :param key_header: ключ из заголовка `X-API-Key` (основной транспорт).
    :return: принятый ключ.
    :raises HTTPException: 403, если ни один из источников не дал валидный ключ.
    """
    for key in (key_header, key_query):
        if key and key in settings.valid_api_keys:
            return key
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid or missing API Key")


async def get_db(
    db: AsyncSession = Depends(get_async_db),
    api_key: str = Depends(get_api_key),
) -> AsyncSession:
    """Legacy dependency: DB + service API key для TG_LMS ботов."""
    # tsk-114: единственный auth-путь generic CRUD-роутера tasks (см.
    # app/api/main.py) — проставляем источник для audit-триггера на
    # tasks.course_id/is_active (app/db/migrations/versions/
    # 20260805_100000_tsk114_task_audit.py). TasksService.bulk_upsert
    # перекрывает более специфичной меткой.
    await set_audit_actor(db, "service:api_key")
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
                    # tsk-432: блокировка действует СРАЗУ, а не после протухания
                    # токена. Пользователь и так грузится из базы на каждом
                    # запросе — проверка бесплатна. 403, а не 401: ключ доступа
                    # исправен, закрыт сам аккаунт, и 401 увёл бы человека на
                    # форму входа по кругу вместо объяснения.
                    if user.blocked_at is not None:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail=user_block_service.BLOCKED_MESSAGE,
                        )
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


# ---------------------------------------------------------------------------
# Централизованный роль-гейт (tsk-298, Фаза 0).
# Один источник проверки НАЛИЧИЯ роли вместо размазанных per-handler проверок.
# ---------------------------------------------------------------------------

def require_role(*role_names: str) -> Callable[..., Awaitable[CurrentUser]]:
    """Фабрика dependency: требует у пользователя хотя бы одну из ролей `role_names`.

    Семантика (совпадает с существующими teacher/methodist-эндпоинтами):
    - сервисный токен (X-API-Key / legacy `?api_key=`) — полный доступ (bypass);
    - обычный пользователь без нужной роли — 403;
    - неаутентифицированный — 401 (даёт `get_current_user`).

    Проверяет только НАЛИЧИЕ роли. Course-tree / student-scoped ACL
    (teacher_course_acl, student_teacher_links) остаётся в хендлерах —
    его этот гейт не заменяет.

    :param role_names: допустимые имена ролей (например, "teacher", "methodist").
    :return: асинхронную FastAPI-dependency, возвращающую CurrentUser.
    """
    allowed: frozenset[str] = frozenset(role_names)

    async def _require_role(
        current_user: CurrentUser = Depends(get_current_user),
        db: AsyncSession = Depends(get_async_db),
    ) -> CurrentUser:
        if current_user.is_service:
            return current_user
        # Ленивый импорт: deps.py грузится очень рано (через users-роутер),
        # а roles_service тянет модели — top-level импорт даёт circular import.
        from app.services import roles_service  # noqa: PLC0415
        user_roles = set(await roles_service.get_user_role_names(db, current_user.id))
        if user_roles.isdisjoint(allowed):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав: требуется роль " + " или ".join(sorted(allowed)),
            )
        return current_user

    return _require_role


# Удобный алиас для самого частого случая — teacher-эндпоинты.
require_teacher = require_role("teacher")


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
