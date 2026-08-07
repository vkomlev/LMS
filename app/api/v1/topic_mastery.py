"""Освоение тем: эндпоинты обзора для методиста (tsk-577).

Гейт только методистский. Преподавателю этот экран не нужен и вреден: он про
правку контента, а решения преподавателя живут в `/learning-gaps/students`.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.services import topic_mastery_service as mastery

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/topic-mastery", tags=["topic_mastery"])

_METHODIST_GATE = require_role("methodist", "admin")

# Окно наблюдения. Верхняя граница есть, потому что запрос идёт по всей
# `task_results`; нижняя — потому что на окне в пару дней любая тема выглядит
# нетронутой.
_MIN_DAYS = 7
_MAX_DAYS = 365
_DEFAULT_DAYS = 90


@router.get("/overview", summary="Освоение по всем темам")
async def overview(
    days: int = Query(_DEFAULT_DAYS, ge=_MIN_DAYS, le=_MAX_DAYS),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_METHODIST_GATE),
) -> dict:
    """Все темы со сдачами: доля верных, охват учениками, темп, признак.

    В отличие от `/learning-gaps/topics` отбора по порогу здесь нет — методисту
    нужна и благополучная тема, и подозрительно лёгкая. Тема с малой выборкой
    остаётся в списке с `reliable: false`.
    """
    return await mastery.topic_overview(db, days=days)


@router.get("/topics/{course_id}", summary="Разбор одной темы")
async def topic_detail(
    course_id: int,
    days: int = Query(_DEFAULT_DAYS, ge=_MIN_DAYS, le=_MAX_DAYS),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_METHODIST_GATE),
) -> dict:
    """Задания темы и ученики темы — контекст для правки.

    Задания и ученики отдаются одним ответом: методист смотрит на них вместе.
    «Задание провальное» и «провальный один ученик» различимы только рядом.
    """
    return {
        "course_id": course_id,
        "days": days,
        "tasks": await mastery.topic_tasks(db, course_id=course_id, days=days),
        "students": await mastery.topic_students(db, course_id=course_id, days=days),
    }
