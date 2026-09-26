"""`POST /me/quiz-funnel/claim` — регистрация из итога квиза-ветки (tsk-1139, Ф2).

SPW зовёт после входа по ссылке из письма, на странице `/quiz/{uid}/done`.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, require_authenticated
from app.core.config import Settings
from app.db.session import get_async_db
from app.schemas.guest_quiz import QuizRecommendation
from app.services import quiz_funnel_claim_service
from app.services.rate_limit_service import get_redis, is_rate_limited

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/me/quiz-funnel", tags=["quiz-funnel"])
_settings = Settings()


class QuizFunnelClaimRequest(BaseModel):
    """Тело claim. guest_session_id — запасной путь, если cookie не дошла."""

    quiz_uid: str = Field(..., min_length=1, max_length=200, description="course_uid квиза-ветки")
    guest_session_id: Optional[UUID] = Field(
        default=None, description="Если не передан — берётся из cookie guest_session"
    )


class QuizFunnelClaimResponse(BaseModel):
    """Полный разбор и куда вести ученика дальше."""

    quiz_uid: str
    branch_code: str
    scales: Dict[str, int]
    recommendation: Optional[QuizRecommendation] = None
    course_id: Optional[int] = Field(
        default=None, description="Рекомендованный курс; открыть его (демо-темы или курс)"
    )
    enrolled: bool = Field(..., description="True — записан на бесплатный курс")
    lead_id: int
    bot_start_url: Optional[str] = Field(default=None, description="Ссылка в бот за PDF ветки")


@router.post(
    "/claim",
    response_model=QuizFunnelClaimResponse,
    responses={
        404: {"description": "Воронка выключена или это не квиз-ветка"},
        409: {"description": "Регистрация из ветки закрыта, квиз не пройден, сессия чужая"},
        422: {"description": "Нет гостевой сессии"},
    },
)
async def claim_quiz_funnel(
    body: QuizFunnelClaimRequest,
    guest_session: str | None = Cookie(default=None),
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> QuizFunnelClaimResponse:
    """Перенести прохождение квиза в кабинет, завести заявку, открыть курс."""
    gs_id = body.guest_session_id
    if gs_id is None and guest_session:
        try:
            gs_id = UUID(guest_session)
        except ValueError:
            gs_id = None
    if gs_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Нет гостевой сессии квиза.")

    redis = get_redis(_settings.redis_url)
    if await is_rate_limited(
        redis, f"quiz_claim:{current_user.id}", max_requests=20, window_seconds=3600
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")

    contact = current_user.email or (f"tg:{current_user.tg_id}" if current_user.tg_id else "")
    try:
        result = await quiz_funnel_claim_service.claim(
            db,
            user_id=current_user.id,
            contact=contact or f"user:{current_user.id}",
            guest_session_id=gs_id,
            quiz_uid=body.quiz_uid,
        )
    except HTTPException:
        await db.rollback()
        raise
    await db.commit()
    return QuizFunnelClaimResponse(**result.__dict__)
