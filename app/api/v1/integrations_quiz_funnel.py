"""`/integrations/quiz-funnel/bot/*` — гость ветки квиза в ученическом боте (tsk-1139, Ф4).

Только сервисный ключ: зовёт бот TG_LMS. tg_id приходит от бота (он его знает
из апдейта Telegram), поэтому каждое действие над строкой гостя сверяет tg_id.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Path, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db
from app.api.v1.integrations_leads import _service_only
from app.auth.current_user import CurrentUser
from app.services import quiz_funnel_bot_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/integrations/quiz-funnel/bot", tags=["quiz-funnel-bot"])


class BotStartRequest(BaseModel):
    token: str = Field(..., min_length=8, max_length=64, description="Часть после q_ в /start")
    tg_id: int
    tg_username: Optional[str] = Field(default=None, max_length=64)


class BotStartResponse(BaseModel):
    bot_lead_id: int
    branch_code: str
    pdf_url: Optional[str] = Field(
        default=None, description="Путь PDF ветки; относительный — от адреса LMS API"
    )
    trial_requested: bool


class BotTgRequest(BaseModel):
    tg_id: int


class BotSentRequest(BaseModel):
    tg_id: int
    step: int = Field(..., ge=0)


class BotDueItem(BaseModel):
    bot_lead_id: int
    tg_id: int
    branch_code: str
    step: int = Field(..., description="0 — демо (+1 день), 1 — пробное (+3), 2 — последнее (+7)")


class BotTrialResponse(BaseModel):
    lead_id: int


@router.post("/start", response_model=BotStartResponse)
async def bot_start(
    body: BotStartRequest,
    _: CurrentUser = Depends(_service_only),
    db: AsyncSession = Depends(get_async_db),
) -> BotStartResponse:
    """/start q_<токен>: привязать гостя к tg и отдать PDF ветки."""
    result = await quiz_funnel_bot_service.start(
        db, token=body.token, tg_id=body.tg_id, tg_username=body.tg_username
    )
    await db.commit()
    return BotStartResponse(**result.__dict__)


@router.get("/due", response_model=List[BotDueItem])
async def bot_due(
    limit: int = Query(50, ge=1, le=200),
    _: CurrentUser = Depends(_service_only),
    db: AsyncSession = Depends(get_async_db),
) -> List[BotDueItem]:
    """Кому пора напомнить. Пусто при выключенных напоминаниях."""
    return [BotDueItem(**d.__dict__) for d in await quiz_funnel_bot_service.list_due(db, limit)]


@router.post("/{bot_lead_id}/sent", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def bot_sent(
    body: BotSentRequest,
    bot_lead_id: int = Path(..., ge=1),
    _: CurrentUser = Depends(_service_only),
    db: AsyncSession = Depends(get_async_db),
) -> Response:
    """Напоминание шага отправлено — назначить следующее."""
    await quiz_funnel_bot_service.mark_sent(
        db, bot_lead_id=bot_lead_id, tg_id=body.tg_id, step=body.step
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{bot_lead_id}/unsubscribe", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def bot_unsubscribe(
    body: BotTgRequest,
    bot_lead_id: int = Path(..., ge=1),
    _: CurrentUser = Depends(_service_only),
    db: AsyncSession = Depends(get_async_db),
) -> Response:
    """Кнопка «Не присылать напоминания»."""
    await quiz_funnel_bot_service.unsubscribe(db, bot_lead_id=bot_lead_id, tg_id=body.tg_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{bot_lead_id}/trial", response_model=BotTrialResponse)
async def bot_trial(
    body: BotTgRequest,
    bot_lead_id: int = Path(..., ge=1),
    _: CurrentUser = Depends(_service_only),
    db: AsyncSession = Depends(get_async_db),
) -> BotTrialResponse:
    """Кнопка «Записаться на пробное»: заявка + стоп напоминаний."""
    lead_id = await quiz_funnel_bot_service.request_trial(
        db, bot_lead_id=bot_lead_id, tg_id=body.tg_id
    )
    await db.commit()
    return BotTrialResponse(lead_id=lead_id)
