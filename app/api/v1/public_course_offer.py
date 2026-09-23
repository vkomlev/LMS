"""Публичная цена курса для лендингов сайта (tsk-1070).

Движок лендингов victor-komlev.ru (ContentBackbone) берёт цену курса из LMS при
каждой публикации лендинга — решение оператора «актуальные цены берём из LMS».
Кабинет маркетолога (`/marketer/pricing/*`) сервисный ключ не пускает намеренно,
поэтому для витрины отдельный открытый путь только на чтение: в ответе лишь то,
что и так пишется на продающей странице.

Без ограничения частоты: запрос дешёвый (два SELECT по ключу), ничего не пишет,
а ответ кешируется на 5 минут (`Cache-Control`).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_db
from app.schemas.pricing import PublicCourseOffer
from app.services import pricing_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/public/courses", tags=["public_course_offer"])

#: Цена меняется редко и вручную; 5 минут — достаточно свежо для публикации лендинга.
_CACHE_CONTROL = "public, max-age=300"


@router.get(
    "/{course_id}/offer",
    response_model=PublicCourseOffer,
    summary="Статус продажи и активные тарифы курса для лендинга сайта",
)
async def get_course_offer(
    response: Response,
    course_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
) -> PublicCourseOffer:
    """Отдать статус продажи курса и активные тарифы его тарифной группы.

    Курса нет — 404. Цена курсу не назначена — `sale_status="unset"`, тарифов нет.
    """
    offer = await pricing_service.get_public_course_offer(db, course_id)
    if offer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Курс не найден")
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return offer
