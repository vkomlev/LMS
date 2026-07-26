"""
Схемы Календаря LMS Фаза 1-2 (tsk-428/tsk-429): часы работы школы, слоты
расписания, occurrence + явка ученика.

Модель данных и границы MVP — docs/specs/2026-07-26-plan-kalendar-lms.md.
Конвенция weekday: 0=понедельник .. 6=воскресенье (Python `date.weekday()`).
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ─── Operating Hours ────────────────────────────────────────────────────────


class OperatingHoursCreate(BaseModel):
    weekday: int = Field(..., ge=0, le=6, description="0=понедельник .. 6=воскресенье")
    start_time: time = Field(..., description="Начало часов работы школы в этот день")
    end_time: time = Field(..., description="Конец часов работы школы в этот день")
    timezone: str = Field(
        default="Europe/Moscow",
        description="IANA timezone; MVP — одна зона на всю школу",
    )

    @model_validator(mode="after")
    def _end_after_start(self) -> "OperatingHoursCreate":
        if self.end_time <= self.start_time:
            raise ValueError("end_time должен быть позже start_time")
        return self


class OperatingHoursUpdate(BaseModel):
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    timezone: Optional[str] = None


class OperatingHoursRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    weekday: int
    start_time: time
    end_time: time
    timezone: str
    created_at: datetime


# ─── Lesson Slot ────────────────────────────────────────────────────────────


class LessonSlotCreate(BaseModel):
    student_id: int = Field(..., description="ID ученика")
    teacher_id: int = Field(..., description="ID преподавателя")
    weekday: int = Field(..., ge=0, le=6, description="0=понедельник .. 6=воскресенье")
    start_time: time = Field(..., description="Время начала занятия")
    duration_minutes: int = Field(..., gt=0, le=480, description="Длительность занятия")
    timezone: str = Field(
        default="Europe/Moscow",
        description="IANA timezone; MVP — одна зона на всю школу",
    )

    @model_validator(mode="after")
    def _distinct_pair(self) -> "LessonSlotCreate":
        if self.student_id == self.teacher_id:
            raise ValueError("student_id и teacher_id должны быть разными пользователями")
        return self


class LessonSlotUpdate(BaseModel):
    """Частичная правка слота. Смена ученика/преподавателя не поддерживается —
    для этого создаётся новый слот (история старого сохраняется через is_active)."""

    weekday: Optional[int] = Field(default=None, ge=0, le=6)
    start_time: Optional[time] = None
    duration_minutes: Optional[int] = Field(default=None, gt=0, le=480)
    timezone: Optional[str] = None
    is_active: Optional[bool] = None


class LessonSlotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    student_id: int
    teacher_id: int
    weekday: int
    start_time: time
    duration_minutes: int
    timezone: str
    is_active: bool
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime


# ─── Lesson Occurrence + явка (tsk-429, Фаза 2) ─────────────────────────────


class LessonOccurrenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slot_id: Optional[int] = None
    student_id: int
    teacher_id: int
    scheduled_at: datetime
    duration_minutes: int
    status: str = Field(
        description="scheduled | confirmed | declined | rescheduled | no_show | completed"
    )
    rescheduled_to_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class AttendanceActionRequest(BaseModel):
    action: Literal["joined", "declined"] = Field(
        ..., description="Ученик подтверждает явку или отказывается"
    )
