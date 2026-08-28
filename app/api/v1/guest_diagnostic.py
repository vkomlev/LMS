"""Гостевая ЕГЭ-диагностика `/api/v1/learning/guest/diagnostic/*` (tsk-053, фаза 2).

Посетитель без регистрации решает восемь коротких задач по темам ЕГЭ и получает карту:
что держится, что просело и с чего начать. Гостевая сессия — та же, что у демо-заданий
и квиза подбора, поэтому после регистрации прохождение так же атрибутируется
через ``POST /me/attribute-guest``.
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
from app.schemas.guest_diagnostic import (
    DiagnosticAnswerRequest,
    DiagnosticAnswerResponse,
    DiagnosticResponse,
    DiagnosticResultResponse,
)
from app.schemas.guest_quiz import QuizLeadRequest, QuizLeadResponse
from app.services import guest_diagnostic_service
from app.services.rate_limit_service import get_redis, is_rate_limited
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/learning/guest/diagnostic", tags=["guest-diagnostic"])
_settings = Settings()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _parse_session(raw: str | None) -> UUID | None:
    """Разобрать cookie мягко: без неё диагностика читается, просто без отметок."""
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def _require_session(raw: str | None) -> UUID:
    """Для записи сессия обязательна: набор задач закреплён именно за ней."""
    parsed = _parse_session(raw)
    if parsed is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Требуется cookie guest_session. Сначала вызовите POST /learning/guest/session.",
        )
    return parsed


# ── GET /learning/guest/diagnostic/{course_uid} ────────────────────────────

@router.get("/{course_uid}", response_model=DiagnosticResponse)
async def get_diagnostic(
    request: Request,
    course_uid: str = Path(..., description="course_uid курса диагностики"),
    guest_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_bare_db),
) -> DiagnosticResponse:
    """Задачи этого посетителя и то, что он уже ответил."""
    ip = _client_ip(request)
    if ip:
        redis = get_redis(_settings.redis_url)
        if await is_rate_limited(redis, f"diag_read:{ip}", max_requests=600, window_seconds=60):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")

    diagnostic = await guest_diagnostic_service.get_diagnostic(
        db, course_uid, _parse_session(guest_session)
    )
    if diagnostic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Диагностика не найдена.")
    return diagnostic


# ── POST /learning/guest/diagnostic/answers ────────────────────────────────

@router.post(
    "/answers",
    response_model=DiagnosticAnswerResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_answer(
    body: DiagnosticAnswerRequest,
    request: Request,
    guest_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_bare_db),
) -> DiagnosticAnswerResponse:
    """Принять решение задачи.

    Лимиты те же, что у квиза: демо-лимиты (5/час/IP, 3/сутки/сессия) берегут платный
    контент, а здесь беречь нечего — зонды написаны специально для витрины и никакого
    платного банка не открывают.
    """
    gs_uuid = _require_session(guest_session)

    redis = get_redis(_settings.redis_url)
    ip = _client_ip(request)
    if ip:
        if await is_rate_limited(redis, f"diag_answer:{ip}", max_requests=200, window_seconds=3600):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много ответов с этого адреса"
            )
    if await is_rate_limited(
        redis, f"diag_answer_session:{gs_uuid}", max_requests=60, window_seconds=86400
    ):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много ответов в этой сессии"
        )

    try:
        result = await guest_diagnostic_service.submit_answer(
            db=db, guest_session_id=gs_uuid, task_id=body.task_id, value=body.value
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


# ── GET /learning/guest/diagnostic/{course_uid}/result ─────────────────────

@router.get("/{course_uid}/result", response_model=DiagnosticResultResponse)
async def get_result(
    request: Request,
    course_uid: str = Path(..., description="course_uid курса диагностики"),
    guest_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_bare_db),
) -> DiagnosticResultResponse:
    """Итог: сколько решено, разбор по темам, что подтянуть и куда идти."""
    ip = _client_ip(request)
    if ip:
        redis = get_redis(_settings.redis_url)
        if await is_rate_limited(redis, f"diag_read:{ip}", max_requests=600, window_seconds=60):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")

    result = await guest_diagnostic_service.get_result(
        db, course_uid, _parse_session(guest_session)
    )
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Диагностика не найдена.")
    return result


# ── POST /learning/guest/diagnostic/{course_uid}/lead ──────────────────────

@router.post(
    "/{course_uid}/lead",
    response_model=QuizLeadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_lead(
    body: QuizLeadRequest,
    request: Request,
    course_uid: str = Path(..., description="course_uid курса диагностики"),
    guest_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_bare_db),
) -> QuizLeadResponse:
    """Принять контакт по итогам диагностики.

    Контракт тела и ответа общий с квизом: для человека это одно и то же действие,
    и разводить две одинаковые схемы значило бы чинить их потом по отдельности.
    """
    gs_uuid = _require_session(guest_session)

    redis = get_redis(_settings.redis_url)
    ip = _client_ip(request)
    if ip:
        if await is_rate_limited(redis, f"diag_lead:{ip}", max_requests=10, window_seconds=3600):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много заявок с этого адреса"
            )
    if await is_rate_limited(
        redis, f"diag_lead_session:{gs_uuid}", max_requests=5, window_seconds=86400
    ):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много заявок в этой сессии"
        )

    try:
        lead_id, already = await guest_diagnostic_service.create_lead(
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
        "tsk-053: заявка с диагностики course_uid=%s lead_id=%s already=%s",
        course_uid, lead_id, already,
    )
    return QuizLeadResponse(lead_id=lead_id, already_submitted=already)
