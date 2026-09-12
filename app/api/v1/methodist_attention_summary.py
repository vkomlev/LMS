"""tsk-888: сводный счётчик для методиста по школе за период.

Побочная находка [[tsk-651]] — методистская панель качества обучения
(`/methodist/quality`) не может показать «кому уделить время» и «кому пора
усложнить» по школе целиком: оба сигнала (tsk-648/tsk-649) считаются только
на одно occurrence. Здесь — тот же расчёт, снятый с другого входа (окно/вся
школа, а не одно занятие); подробности и разбор курсового разреза — в
``teacher_lesson_summary_service.get_school_attention_summary`` /
``get_school_ready_for_harder_summary``.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.services import teacher_lesson_summary_service

router = APIRouter(prefix="/methodist", tags=["methodist_attention_summary"])

_METHODIST_GATE = require_role("methodist", "admin")


class AttentionSummaryRead(BaseModel):
    window_days: int
    window_from: Any
    window_to: Any
    participants_in_window: int
    students_flagged: int
    by_reason: dict[str, int]


class ReadyForHarderItem(BaseModel):
    student_id: int
    full_name: Optional[str] = None
    tasks: int
    first_try_ok: int
    percent: int
    hard_tasks: int
    window_days: int
    detail: str


class ReadyForHarderSummaryRead(BaseModel):
    window_days: int
    active_students: int
    students: list[ReadyForHarderItem]


@router.get("/attention-summary", response_model=AttentionSummaryRead)
async def get_attention_summary(
    window_days: int = Query(
        default=7, ge=1, le=30,
        description="Окно накопления, дней. По умолчанию — неделя (tsk-888).",
    ),
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(_METHODIST_GATE),
) -> AttentionSummaryRead:
    """Сколько учеников за окно получили повод «уделить время» (tsk-648) —
    по школе целиком, без разреза по курсам (в схеме курс occurrence не
    хранит, честной опоры для разреза сегодня нет — см. docstring сервиса)."""
    data = await teacher_lesson_summary_service.get_school_attention_summary(
        db, window_days=window_days,
    )
    return AttentionSummaryRead(**data)


@router.get("/ready-for-harder-summary", response_model=ReadyForHarderSummaryRead)
async def get_ready_for_harder_summary(
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(_METHODIST_GATE),
) -> ReadyForHarderSummaryRead:
    """Кому пора усложнить (tsk-649) — по всем активным ученикам школы, а не
    только тем, кто попал в одно occurrence."""
    data = await teacher_lesson_summary_service.get_school_ready_for_harder_summary(db)
    return ReadyForHarderSummaryRead(**data)
