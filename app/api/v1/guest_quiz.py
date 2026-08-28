"""Гостевой квиз-лид-магнит `/api/v1/learning/guest/quiz/*` (tsk-053, фаза 1).

Посетитель без регистрации проходит короткий опрос, получает рекомендацию программы
и уходит записываться. Гостевая сессия — та же, что у демо-заданий (cookie
``guest_session``), поэтому после регистрации прохождение квиза так же
атрибутируется к учётной записи через ``POST /me/attribute-guest``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Path, Request, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db
from app.core.config import Settings
from app.models.guest_session import GuestSession
from app.schemas.guest_quiz import (
    QuizAnswerRequest,
    QuizAnswerResponse,
    QuizLeadRequest,
    QuizLeadResponse,
    QuizResponse,
    QuizResultResponse,
)
from app.services import guest_quiz_service
from app.services.rate_limit_service import get_redis, is_rate_limited
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/learning/guest/quiz", tags=["guest-quiz"])
_settings = Settings()

_GUEST_SESSION_COOKIE = "guest_session"


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _parse_session(raw: str | None) -> UUID | None:
    """Разобрать cookie мягко: без неё квиз читается, просто без отметок ответов."""
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def _require_session(raw: str | None) -> UUID:
    """Для записи сессия обязательна: иначе ответ некуда отнести."""
    parsed = _parse_session(raw)
    if parsed is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Требуется cookie guest_session. Сначала вызовите POST /learning/guest/session.",
        )
    return parsed


# ── GET /learning/guest/quiz/{course_uid} ──────────────────────────────────

@router.get("/{course_uid}", response_model=QuizResponse)
async def get_quiz(
    request: Request,
    course_uid: str = Path(..., description="course_uid курса-квиза"),
    guest_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_bare_db),
) -> QuizResponse:
    """Вопросы квиза и уже выбранные варианты этой гостевой сессии."""
    ip = _client_ip(request)
    if ip:
        redis = get_redis(_settings.redis_url)
        if await is_rate_limited(redis, f"quiz_read:{ip}", max_requests=600, window_seconds=60):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")

    quiz = await guest_quiz_service.get_quiz(db, course_uid, _parse_session(guest_session))
    if quiz is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Квиз не найден.")
    return quiz


# ── POST /learning/guest/quiz/answers ──────────────────────────────────────

@router.post(
    "/answers",
    response_model=QuizAnswerResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_quiz_answer(
    body: QuizAnswerRequest,
    request: Request,
    guest_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_bare_db),
) -> QuizAnswerResponse:
    """Принять ответ на вопрос квиза.

    Лимиты щедрее, чем у демо-заданий (5/час/IP, 3/сутки/сессия): там лимит бережёт
    платный контент, здесь бы он просто не дал пройти опрос из шести вопросов —
    и уж тем более пройти второй квиз с того же адреса.
    """
    gs_uuid = _require_session(guest_session)

    redis = get_redis(_settings.redis_url)
    ip = _client_ip(request)
    if ip:
        if await is_rate_limited(redis, f"quiz_answer:{ip}", max_requests=200, window_seconds=3600):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много ответов с этого адреса"
            )
    if await is_rate_limited(
        redis, f"quiz_answer_session:{gs_uuid}", max_requests=60, window_seconds=86400
    ):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много ответов в этой сессии"
        )

    try:
        result = await guest_quiz_service.submit_quiz_answer(
            db=db,
            guest_session_id=gs_uuid,
            task_id=body.task_id,
            selected_option_ids=body.selected_option_ids,
        )
    except DomainError:
        await db.rollback()
        raise

    await db.execute(
        update(GuestSession)
        .where(GuestSession.id == gs_uuid)
        .values(last_used_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return result


# ── GET /learning/guest/quiz/{course_uid}/result ───────────────────────────

@router.get("/{course_uid}/result", response_model=QuizResultResponse)
async def get_quiz_result(
    request: Request,
    course_uid: str = Path(..., description="course_uid курса-квиза"),
    guest_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_bare_db),
) -> QuizResultResponse:
    """Итог квиза: рекомендация программы и ссылка на запись.

    Ограничение частоты такое же, как у чтения вопросов: страница публичная и на
    неё будет идти реклама, а итог считает шкалы и перебирает правила — самая
    тяжёлая из четырёх ручек.
    """
    ip = _client_ip(request)
    if ip:
        redis = get_redis(_settings.redis_url)
        if await is_rate_limited(redis, f"quiz_read:{ip}", max_requests=600, window_seconds=60):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")

    result = await guest_quiz_service.get_quiz_result(
        db, course_uid, _parse_session(guest_session)
    )
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Квиз не найден.")
    return result


# ── POST /learning/guest/quiz/{course_uid}/lead ────────────────────────────

@router.post(
    "/{course_uid}/lead",
    response_model=QuizLeadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_quiz_lead(
    body: QuizLeadRequest,
    request: Request,
    course_uid: str = Path(..., description="course_uid курса-квиза"),
    guest_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_bare_db),
) -> QuizLeadResponse:
    """Принять контакт по итогам квиза.

    Ручка публичная и пишущая, поэтому лимит здесь строже, чем на ответах: 10 заявок
    в час с адреса и 5 в сутки с сессии — живому человеку хватает с запасом даже
    если он передумал и поправил телефон, а на спам-рассылку заявок не разгонишься.
    """
    gs_uuid = _require_session(guest_session)

    redis = get_redis(_settings.redis_url)
    ip = _client_ip(request)
    if ip:
        if await is_rate_limited(redis, f"quiz_lead:{ip}", max_requests=10, window_seconds=3600):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много заявок с этого адреса"
            )
    if await is_rate_limited(
        redis, f"quiz_lead_session:{gs_uuid}", max_requests=5, window_seconds=86400
    ):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много заявок в этой сессии"
        )

    try:
        lead_id, already = await guest_quiz_service.create_quiz_lead(
            db=db,
            course_uid=course_uid,
            guest_session_id=gs_uuid,
            contact=body.contact.strip(),
            full_name=(body.full_name or "").strip() or None,
        )
    except DomainError:
        await db.rollback()
        raise

    await db.commit()
    logger.info(
        "tsk-053: заявка с квиза course_uid=%s lead_id=%s already=%s",
        course_uid, lead_id, already,
    )
    return QuizLeadResponse(lead_id=lead_id, already_submitted=already)
