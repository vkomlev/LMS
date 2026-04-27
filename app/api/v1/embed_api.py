"""Embed API — публичные эндпоинты для виджета SPW (без auth)."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db
from app.models.guest_attempt import GuestAttempt
from app.models.guest_session import GuestSession
from app.services.rate_limit_service import get_redis, is_rate_limited
from app.core.config import Settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/embed", tags=["embed"])
_settings = Settings()


class GuestSessionResponse(BaseModel):
    guest_session_id: str


class GuestAttemptRequest(BaseModel):
    task_id: int | None = None
    answer_json: dict
    is_correct: bool | None = None


class GuestAttemptResponse(BaseModel):
    attempt_id: int


@router.post("/session", response_model=GuestSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_guest_session(
    request: Request,
    db: AsyncSession = Depends(get_bare_db),
) -> GuestSessionResponse:
    """Создать анонимную guest-сессию (для виджета без авторизации)."""
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    redis = get_redis(_settings.redis_url)
    if ip and await is_rate_limited(redis, f"guest_session:{ip}", max_requests=10, window_seconds=3600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")

    session = GuestSession(ip=ip, ua_fingerprint=ua)
    db.add(session)
    await db.flush()
    await db.commit()
    return GuestSessionResponse(guest_session_id=str(session.id))


@router.post(
    "/session/{guest_session_id}/attempts",
    response_model=GuestAttemptResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_guest_attempt(
    guest_session_id: str,
    body: GuestAttemptRequest,
    request: Request,
    db: AsyncSession = Depends(get_bare_db),
) -> GuestAttemptResponse:
    """Записать попытку ответа гостя."""
    ip = request.client.host if request.client else None
    redis = get_redis(_settings.redis_url)
    if ip and await is_rate_limited(redis, f"guest_attempt:{ip}", max_requests=60, window_seconds=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")

    attempt = GuestAttempt(
        guest_session_id=guest_session_id,
        task_id=body.task_id,
        answer_json=body.answer_json,
        is_correct=body.is_correct,
    )
    db.add(attempt)
    await db.flush()
    await db.commit()
    return GuestAttemptResponse(attempt_id=attempt.id)
