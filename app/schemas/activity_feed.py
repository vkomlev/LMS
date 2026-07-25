"""Схема API «лента активности учеников» для преподавателя (tsk-408).

Единый поток событий по всем ученикам преподавателя (решение задания,
запрос помощи, изучение материала), отсортированный по времени.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

ActivityEventType = Literal["task_solved", "help_requested", "material_studied"]


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
            "help_requested: open|closed; material_studied: всегда null"
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
