"""Сигналы «нужно повторение»: эндпоинты (tsk-572, фаза 7).

Два разных списка для двух разных людей — см. `learning_gap_signals_service`.
Преподаватель видит своих учеников и может ответить: разберусь сам / передать
методисту / это ложное срабатывание. Методист видит темы.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.services import learning_gap_signals_service as signals

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/learning-gaps", tags=["learning_gaps"])

# Ученические сигналы — преподавателю (и методисту с админом: им полезно
# видеть картину целиком). Темы — методисту: заводить мини-курс всё равно ему.
_TEACHER_GATE = require_role("teacher", "methodist", "admin")
_METHODIST_GATE = require_role("methodist", "admin")


class GapSignalRead(BaseModel):
    id: int
    course_id: int
    course_title: str
    student_id: Optional[int] = None
    student_name: Optional[str] = None
    submissions: int
    students: int
    wrong_rate: float
    status: str
    teacher_comment: Optional[str] = None

    @property
    def wrong_percent(self) -> int:
        return round(self.wrong_rate * 100)


class SignalDecision(BaseModel):
    """Ответ преподавателя на сигнал."""

    comment: Optional[str] = Field(
        None, max_length=2000,
        description=(
            "Что преподаватель знает про ученика сверх цифр — он видел его "
            "вживую. Уезжает вместе с эскалацией методисту."
        ),
    )
    escalate: bool = Field(
        False,
        description=(
            "False — «разберусь сам на занятии» (нормальный исход: живой канал "
            "есть только у преподавателя). True — передать методисту."
        ),
    )


def _view(rows: list[dict]) -> list[dict]:
    """Добавить процент: доля в интерфейсе читается хуже, чем «78%»."""
    out = []
    for r in rows:
        item = dict(r)
        item["wrong_percent"] = round(float(r["wrong_rate"]) * 100)
        out.append(item)
    return out


@router.get("/students", summary="Кому нужно повторение (преподавателю)")
async def list_student_signals(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_TEACHER_GATE),
) -> list[dict]:
    """Ученики, которым датчик предлагает повторение.

    Показываются только открытые сигналы: разобранные уходят из списка, иначе
    он копится и его перестают читать.
    """
    return _view(await signals.list_signals(db, for_student=True))


@router.get("/topics", summary="Темы под мини-курс (методисту)")
async def list_topic_signals(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_METHODIST_GATE),
) -> list[dict]:
    """Темы, на которых спотыкается сразу несколько учеников."""
    return _view(await signals.list_signals(db, for_student=False))


@router.post("/{signal_id}/acknowledge", summary="Принять к сведению")
async def acknowledge(
    signal_id: int,
    body: SignalDecision,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_TEACHER_GATE),
) -> dict:
    """Преподаватель разобрался с сигналом.

    Без эскалации — «беру на себя, разберём на занятии». С эскалацией сигнал
    уходит методисту ВМЕСТЕ с комментарием: цифру он и так видит, ценность в
    том, что преподаватель знает про ученика живьём.
    """
    ok = await signals.acknowledge_signal(
        db, signal_id=signal_id, teacher_id=current_user.id,
        comment=body.comment, escalate=body.escalate,
    )
    if not ok:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Сигнал не найден или уже закрыт",
        )
    return {"status": "escalated" if body.escalate else "acknowledged"}


@router.post("/{signal_id}/dismiss", summary="Повторение не нужно")
async def dismiss(
    signal_id: int,
    body: SignalDecision,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_TEACHER_GATE),
) -> dict:
    """Отклонить сигнал.

    Причина сохраняется намеренно: по отклонениям видно, что датчик шумит
    (ученик болел, сломан эталон), и это основание пересмотреть пороги, а не
    молча терпеть ложные срабатывания.
    """
    ok = await signals.dismiss_signal(
        db, signal_id=signal_id, teacher_id=current_user.id, comment=body.comment
    )
    if not ok:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Сигнал не найден или уже закрыт"
        )
    return {"status": "dismissed"}
