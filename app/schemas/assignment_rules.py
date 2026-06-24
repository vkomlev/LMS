"""
Схемы назначения курсов (tsk-031): ручное назначение учителем и чтение событий.

Модель данных — docs/ai/adr/0002-course-assignment-trigger-rules.md.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ManualAssignRequest(BaseModel):
    """
    Запрос на ручное назначение курса ученику учителем (в один клик).

    Нужно указать ровно один идентификатор курса: ``course_id`` или ``course_uid``.
    """

    course_id: Optional[int] = Field(
        default=None,
        description="ID курса для назначения.",
        examples=[12, None],
    )
    course_uid: Optional[str] = Field(
        default=None,
        description="course_uid курса (например 'wp:vvodnyy-python').",
        examples=["wp:vvodnyy-python", None],
    )
    reason: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Причина назначения (для журнала, опционально).",
        examples=["Не освоил тему циклов", None],
    )

    @model_validator(mode="after")
    def _exactly_one_course_ref(self) -> "ManualAssignRequest":
        """Ровно один из course_id / course_uid должен быть задан."""
        has_id = self.course_id is not None
        has_uid = bool(self.course_uid and self.course_uid.strip())
        if has_id == has_uid:
            raise ValueError("Укажите ровно один из параметров: course_id или course_uid")
        return self


class ManualAssignResponse(BaseModel):
    """Результат ручного назначения курса."""

    student_id: int = Field(..., description="ID ученика")
    course_id: int = Field(..., description="ID назначенного курса")
    already_enrolled: bool = Field(
        ...,
        description="True, если ученик уже был привязан к курсу (повторный клик).",
    )
    event_id: Optional[int] = Field(
        default=None,
        description="ID записи в assignment_event (None, если событие не создавалось).",
    )


class AssignmentEventRead(BaseModel):
    """Запись журнала назначений (для чтения/аудита)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    student_id: int
    assigned_course_id: int
    rule_id: Optional[int] = None
    source: str
    assigned_by: Optional[int] = None
    attempt_id: Optional[int] = None
    task_result_id: Optional[int] = None
    already_enrolled: bool
    detail: Optional[dict] = None
    created_at: datetime
