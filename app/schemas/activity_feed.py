"""Схема API «лента активности учеников» для преподавателя (tsk-408).

Единый поток событий по всем ученикам преподавателя (решение задания,
запрос помощи, изучение материала, простой на занятии), отсортированный
по времени.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.code_review import CodeReviewBadge

ActivityEventType = Literal[
    "task_solved", "help_requested", "material_studied", "student_idle"
]


class ActivityFeedEvent(BaseModel):
    """Одно событие в ленте активности."""

    type: ActivityEventType
    student_id: int
    student_name: Optional[str] = None
    task_id: Optional[int] = None
    material_id: Optional[int] = None
    course_id: Optional[int] = None
    timestamp: datetime
    summary: str = Field(..., description="Человекочитаемое описание события")
    outcome: Optional[str] = Field(
        default=None,
        description=(
            "task_solved: correct|incorrect|pending_review; "
            "help_requested: open|closed; material_studied: всегда null; "
            "student_idle: ongoing (простой продолжается) | resolved (вернулся)"
        ),
    )
    code_review: Optional[CodeReviewBadge] = Field(
        default=None,
        description=(
            "tsk-302: машинная оценка кода в компактном виде — только у событий "
            "task_solved по заданиям с кодом. Null у остальных типов событий и у "
            "работ, где оценки нет. Ученику лента недоступна"
        ),
    )


class ActivityFeedResponse(BaseModel):
    """Лента активности, топ-N по убыванию времени."""

    events: List[ActivityFeedEvent] = Field(...)
    has_more: bool = Field(
        ..., description="Есть ли более старые события за пределами текущей страницы"
    )
    next_before: Optional[datetime] = Field(
        default=None,
        description="Курсор для следующей страницы («показать ещё») — передать в `before`",
    )
