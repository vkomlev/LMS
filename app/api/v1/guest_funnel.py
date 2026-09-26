"""Квиз-воронка сайта `/api/v1/learning/guest/funnel/*` (tsk-1139).

Квиз с развилкой «для кого подбираем» и ветками по правилам контента. Гостевая
сессия — та же cookie ``guest_session``, что у квиза и демо-заданий. При
выключенной воронке (или у курса без спецификации) все ручки отвечают 404 —
SPW тогда показывает обычный квиз.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db
from app.core.config import Settings
from app.services import quiz_funnel_service
from app.services.quiz_funnel_service import Funnel
from app.services.rate_limit_service import get_redis, is_rate_limited
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/learning/guest/funnel", tags=["guest-funnel"])
_settings = Settings()


# ── схемы ──────────────────────────────────────────────────────────────────

class FunnelOption(BaseModel):
    id: str
    text: str


class FunnelQuestion(BaseModel):
    code: str
    branch: str
    type: str = Field(..., description="SC_Qw — один вариант, MC_Qw — несколько")
    stem: str = Field(..., description="Формулировка голосом роли (родителю — «ваш ребёнок»)")
    options: List[FunnelOption]
    code_snippet: Optional[Dict[str, Any]] = Field(
        default=None, description="Код к мини-проверке: {lang, text}"
    )
    selected_option_ids: Optional[List[str]] = None
    feedback: Optional[str] = Field(default=None, description="Разбор мини-проверки после ответа")


class FunnelState(BaseModel):
    """Видимые вопросы до текущего включительно. Путь зависит от ответов,
    поэтому после каждого ответа список перезапрашивается."""

    quiz_uid: str
    title: str
    description: Optional[str] = None
    branch: str
    role: Optional[str] = None
    questions: List[FunnelQuestion]
    answered_count: int
    remaining_estimate: int = Field(..., description="Сколько вопросов примерно осталось в ветке")
    is_complete: bool
    feedback: Optional[str] = Field(default=None, description="Разбор только что отвеченной проверки")


class FunnelStartRequest(BaseModel):
    branch: Optional[str] = Field(default=None, max_length=32)
    role: Optional[str] = Field(default=None, max_length=32)
    dir: Optional[str] = Field(default=None, max_length=32)
    attribution: Dict[str, str] = Field(
        default_factory=dict, description="utm_*, page, referrer; прочие ключи отбрасываются"
    )


class FunnelAnswerRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    selected_option_ids: List[str] = Field(..., min_length=1, max_length=20)


class FunnelButton(BaseModel):
    id: Optional[str] = None
    text: Optional[str] = None
    kind: Optional[str] = Field(
        default=None,
        description="register | demo | lead_trial | telegram_bot | link | share_parent_link | contact",
    )
    target: Optional[str] = Field(default=None, description="course_uid для link")
    url: Optional[str] = Field(default=None, description="Готовый адрес (бот, переписка)")


class FunnelResult(BaseModel):
    """Видимая гостю часть итога. Полный разбор — POST /me/quiz-funnel/claim."""

    quiz_uid: str
    is_complete: bool
    outcome_code: Optional[str] = None
    branch: Optional[str] = None
    role: Optional[str] = None
    title: Optional[str] = None
    visible: List[str] = Field(default_factory=list)
    buttons: List[FunnelButton] = Field(default_factory=list)
    target_course_uid: Optional[str] = None
    registration_enabled: bool = False
    bot_start_url: Optional[str] = None
    contact_url: Optional[str] = None


class FunnelLeadRequest(BaseModel):
    contact: str = Field(..., min_length=3, max_length=200,
                         description="Телефон, почта или ник — как удобнее")
    full_name: Optional[str] = Field(default=None, max_length=200)


class FunnelLeadResponse(BaseModel):
    lead_id: int


# ── помощники ──────────────────────────────────────────────────────────────

def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def _parse_session(raw: Optional[str]) -> Optional[UUID]:
    try:
        return UUID(raw) if raw else None
    except ValueError:
        return None


def _require_session(raw: Optional[str]) -> UUID:
    parsed = _parse_session(raw)
    if parsed is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Требуется cookie guest_session. Сначала вызовите POST /learning/guest/session.",
        )
    return parsed


async def _funnel(db: AsyncSession, uid: str) -> Funnel:
    funnel = await quiz_funnel_service.load_funnel(db, uid)
    if funnel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Квиз-воронка не найдена.")
    return funnel


async def _limit(request: Request, key: str, max_requests: int, window: int) -> None:
    ip = _client_ip(request)
    if not ip:
        return
    redis = get_redis(_settings.redis_url)
    if await is_rate_limited(redis, f"{key}:{ip}", max_requests=max_requests, window_seconds=window):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")


# ── ручки ──────────────────────────────────────────────────────────────────

@router.get("/{course_uid}", response_model=FunnelState)
async def get_funnel_state(
    request: Request,
    course_uid: str = Path(..., description="course_uid квиза-воронки"),
    guest_session: Optional[str] = Cookie(default=None),
    db: AsyncSession = Depends(get_bare_db),
) -> FunnelState:
    """Видимые вопросы и текущий шаг."""
    await _limit(request, "funnel_read", 600, 60)
    funnel = await _funnel(db, course_uid)
    return FunnelState(**await quiz_funnel_service.get_state(db, funnel, _parse_session(guest_session)))


@router.post("/{course_uid}/start", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def start_funnel(
    body: FunnelStartRequest,
    request: Request,
    course_uid: str = Path(...),
    guest_session: Optional[str] = Cookie(default=None),
    db: AsyncSession = Depends(get_bare_db),
) -> None:
    """Параметры ссылки (branch/role/dir) и метки первого касания."""
    await _limit(request, "funnel_start", 60, 3600)
    gs = _require_session(guest_session)
    funnel = await _funnel(db, course_uid)
    await quiz_funnel_service.start(
        db, funnel, gs, {"branch": body.branch, "role": body.role, "dir": body.dir}, body.attribution
    )
    await db.commit()


@router.post("/{course_uid}/answer", response_model=FunnelState)
async def answer_funnel(
    body: FunnelAnswerRequest,
    request: Request,
    course_uid: str = Path(...),
    guest_session: Optional[str] = Cookie(default=None),
    db: AsyncSession = Depends(get_bare_db),
) -> FunnelState:
    """Ответ на вопрос текущего пути. Возвращает новый путь и разбор проверки."""
    await _limit(request, "funnel_answer", 300, 3600)
    gs = _require_session(guest_session)
    funnel = await _funnel(db, course_uid)
    try:
        state = await quiz_funnel_service.submit_answer(
            db, funnel, gs, body.code, body.selected_option_ids
        )
    except DomainError:
        await db.rollback()
        raise
    await db.commit()
    return FunnelState(**state)


@router.get("/{course_uid}/result", response_model=FunnelResult)
async def get_funnel_result(
    request: Request,
    course_uid: str = Path(...),
    guest_session: Optional[str] = Cookie(default=None),
    db: AsyncSession = Depends(get_bare_db),
) -> FunnelResult:
    """Видимая часть итога и кнопки (заводит токен бота и фиксирует итог для замеров)."""
    await _limit(request, "funnel_read", 600, 60)
    funnel = await _funnel(db, course_uid)
    result = await quiz_funnel_service.get_result(db, funnel, _parse_session(guest_session))
    await db.commit()
    return FunnelResult(**result)


@router.post("/{course_uid}/lead", response_model=FunnelLeadResponse,
             status_code=status.HTTP_201_CREATED)
async def submit_funnel_lead(
    body: FunnelLeadRequest,
    request: Request,
    course_uid: str = Path(...),
    guest_session: Optional[str] = Cookie(default=None),
    db: AsyncSession = Depends(get_bare_db),
) -> FunnelLeadResponse:
    """Запись на пробное с итога: заявка с веткой, итогом и метками.

    Лимит строже чтения — ручка публичная и пишущая (как у заявки квиза).
    """
    await _limit(request, "funnel_lead", 10, 3600)
    gs = _require_session(guest_session)
    funnel = await _funnel(db, course_uid)
    try:
        lead_id = await quiz_funnel_service.submit_lead(
            db, funnel, gs, body.contact.strip(), (body.full_name or "").strip() or None
        )
    except DomainError:
        await db.rollback()
        raise
    await db.commit()
    logger.info("tsk-1139: заявка с итога квиза %s lead_id=%s", course_uid, lead_id)
    return FunnelLeadResponse(lead_id=lead_id)
