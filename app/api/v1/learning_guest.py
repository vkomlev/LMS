"""Guest-mode эндпоинты `/api/v1/learning/guest/*` (Phase Y-5).

Анонимный посетитель SPW решает 1+ задач из public-demo курса без
регистрации; впоследствии после login/registration все его попытки
атрибутируются к user_id (см. /me/attribute-guest и Y-1 atribution
в auth-handlers).

См. tech-spec Y-5 §6.2.
"""
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Path, Request, Response, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db
from app.core.config import Settings
from app.models.guest_session import GuestSession
from app.schemas.learning_guest import (
    GuestAttemptCreateRequest,
    GuestAttemptCreateResponse,
    GuestCourseInfoResponse,
    GuestSessionCreateResponse,
    GuestTaskResponse,
)
from app.services import learning_guest_service
from app.services.rate_limit_service import get_redis, is_rate_limited
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/learning/guest", tags=["learning-guest"])
_settings = Settings()

_GUEST_SESSION_COOKIE = "guest_session"
_GUEST_SESSION_TTL_DAYS = 30
_GUEST_SESSION_TTL_SEC = _GUEST_SESSION_TTL_DAYS * 24 * 3600


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ── POST /learning/guest/session ───────────────────────────────────────────

@router.post(
    "/session",
    response_model=GuestSessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_guest_session(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_bare_db),
) -> GuestSessionCreateResponse:
    """Создать анонимную guest-сессию и установить cookie."""
    ip = _client_ip(request)
    ua = request.headers.get("user-agent")

    if ip:
        redis = get_redis(_settings.redis_url)
        if await is_rate_limited(redis, f"guest_session:{ip}", max_requests=10, window_seconds=3600):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")

    session = GuestSession(ip=ip, ua_fingerprint=ua)
    db.add(session)
    await db.flush()
    await db.commit()

    expires_at = datetime.now(timezone.utc) + timedelta(days=_GUEST_SESSION_TTL_DAYS)
    response.set_cookie(
        key=_GUEST_SESSION_COOKIE,
        value=str(session.id),
        max_age=_GUEST_SESSION_TTL_SEC,
        httponly=True,
        secure=_settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return GuestSessionCreateResponse(guest_session_id=session.id, expires_at=expires_at)


# ── GET /learning/guest/courses/{course_uid} ───────────────────────────────

@router.get(
    "/courses/{course_uid}",
    response_model=GuestCourseInfoResponse,
)
async def get_guest_course_info(
    course_uid: str = Path(..., description="course_uid публичного demo-курса"),
    db: AsyncSession = Depends(get_bare_db),
) -> GuestCourseInfoResponse:
    """Вернуть info о demo-курсе (для SPW UX)."""
    info = await learning_guest_service.get_demo_course_info(db, course_uid)
    if info is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Курс не найден среди публичных демо.",
        )
    return info


# ── GET /learning/guest/task/{task_id} ─────────────────────────────────────

@router.get(
    "/task/{task_id}",
    response_model=GuestTaskResponse,
)
async def get_guest_task(
    request: Request,
    task_id: int = Path(..., description="ID задачи"),
    guest_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_bare_db),
) -> GuestTaskResponse:
    """Загрузить stem + варианты задачи. Только для public-demo курсов.

    correct_answer / solution_rules в payload отсутствуют (защита от слива).
    SA_COM/TA задачи отдают 404 (нет teacher review без user).
    """
    ip = _client_ip(request)
    if ip:
        redis = get_redis(_settings.redis_url)
        if await is_rate_limited(redis, f"guest_read:{ip}", max_requests=600, window_seconds=60):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")

    payload = await learning_guest_service.get_demo_task(db, task_id)
    if payload is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Задача не найдена в публичных демо-курсах.",
        )

    # Best-effort touch last_used_at (не блокируем выдачу при ошибке)
    if guest_session:
        try:
            gs_uuid = UUID(guest_session)
            await db.execute(
                update(GuestSession)
                .where(GuestSession.id == gs_uuid)
                .values(last_used_at=datetime.now(timezone.utc))
            )
            await db.commit()
        except (ValueError, Exception) as exc:  # noqa: BLE001 — best-effort
            logger.debug("guest_task: не удалось обновить last_used_at: %s", exc)

    return payload


# ── POST /learning/guest/attempts ──────────────────────────────────────────

@router.post(
    "/attempts",
    response_model=GuestAttemptCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_guest_attempt(
    body: GuestAttemptCreateRequest,
    request: Request,
    guest_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_bare_db),
) -> GuestAttemptCreateResponse:
    """Принять ответ гостя на demo-задачу.

    Cookie `guest_session` обязательна; иначе 400.
    Rate-limit: 5/час/IP И 3/сутки/(guest_session) — оба должны пройти.
    """
    if not guest_session:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Требуется cookie guest_session. Сначала вызовите POST /learning/guest/session.",
        )
    try:
        gs_uuid = UUID(guest_session)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Некорректный формат guest_session cookie.",
        ) from exc

    ip = _client_ip(request)
    redis = get_redis(_settings.redis_url)
    if ip:
        if await is_rate_limited(redis, f"guest_attempt:{ip}", max_requests=5, window_seconds=3600):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много попыток с этого IP")
    if await is_rate_limited(
        redis,
        f"guest_attempt_session:{gs_uuid}",
        max_requests=3,
        window_seconds=86400,
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много попыток в этой сессии")

    try:
        attempt_id, check_result = await learning_guest_service.submit_guest_attempt(
            db=db,
            guest_session_id=gs_uuid,
            task_id=body.task_id,
            answer=body.answer,
        )
    except DomainError:
        await db.rollback()
        raise

    await db.commit()
    return GuestAttemptCreateResponse(
        attempt_id=attempt_id,
        is_correct=bool(check_result.is_correct),
        score=check_result.score,
        max_score=check_result.max_score,
    )
