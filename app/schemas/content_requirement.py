from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RequirementLevel = Literal["skippable", "recommended", "required"]


class RequirementReason(BaseModel):
    """tsk-1361: почему уровень элемента для ученика отличается от курса."""

    code: str = Field(
        ...,
        description=(
            "grace_new_content — добавлено после прохождения темы; grace_ahead — "
            "добавлено, когда ученик уже ушёл дальше; program_trim — не входит в "
            "программу до экзамена; teacher — решение преподавателя"
        ),
    )
    text: str = Field(..., description="Готовая строка для показа ученику и учителю")
    source: Literal["rule", "human"] = Field(..., description="rule — правило, human — преподаватель")
