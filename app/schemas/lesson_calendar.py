"""
Схемы Календаря LMS (tsk-428/429/430/435): часы работы школы, групповые
слоты и их участники, occurrence + явка по участнику.

Модель данных и границы MVP — docs/specs/2026-07-26-plan-kalendar-lms.md +
tsk-435 (rework на группы после встречи с реальными данными импорта).
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


# ─── Lesson Slot (групповой, tsk-435) ───────────────────────────────────────


class LessonSlotCreate(BaseModel):
    teacher_id: int = Field(..., description="ID преподавателя")
    weekday: int = Field(..., ge=0, le=6, description="0=понедельник .. 6=воскресенье")
    start_time: time = Field(..., description="Время начала занятия")
    duration_minutes: int = Field(..., gt=0, le=480, description="Длительность занятия")
    timezone: str = Field(
        default="Europe/Moscow",
        description="IANA timezone; MVP — одна зона на всю школу",
    )
    student_ids: list[int] = Field(
        default_factory=list,
        description="Начальные участники слота (опционально, удобно для импорта)",
    )


class LessonSlotUpdate(BaseModel):
    """Частичная правка слота (без участников — см. отдельные эндпоинты
    `/lesson-slots/{id}/participants`)."""

    weekday: Optional[int] = Field(default=None, ge=0, le=6)
    start_time: Optional[time] = None
    duration_minutes: Optional[int] = Field(default=None, gt=0, le=480)
    timezone: Optional[str] = None
    is_active: Optional[bool] = None


class LessonSlotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    teacher_id: int
    weekday: int
    start_time: time
    duration_minutes: int
    timezone: str
    is_active: bool
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    student_ids: list[int] = Field(
        default_factory=list, description="Активные участники слота (заполняется на уровне API)"
    )


class AddSlotParticipantRequest(BaseModel):
    student_id: int = Field(..., description="Ученик, добавляемый в групповой слот")


class SlotParticipantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slot_id: int
    student_id: int
    is_active: bool
    added_by: Optional[int] = None
    created_at: datetime


# ─── Lesson Occurrence + участники (tsk-429/430/435) ───────────────────────


class LessonOccurrenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slot_id: Optional[int] = None
    teacher_id: int
    scheduled_at: datetime
    duration_minutes: int
    created_at: datetime
    updated_at: datetime


class ParticipantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    occurrence_id: int
    student_id: int
    status: str = Field(
        description="scheduled | confirmed | declined | rescheduled | no_show | completed"
    )
    rescheduled_to_occurrence_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class MyLessonOccurrenceRead(LessonOccurrenceRead):
    """Occurrence с точки зрения ОДНОГО ученика — его личный статус участия,
    без списка остальных участников группы (приватность)."""

    participant_id: int
    my_status: str = Field(
        description="scheduled | confirmed | declined | rescheduled | no_show | completed"
    )


class TeacherParticipantRead(ParticipantRead):
    is_overdue: bool = Field(
        description=(
            "status='scheduled' и порог 'не пришёл' уже истёк — считается "
            "живым запросом, не ждёт следующего cron-тика"
        )
    )


class TeacherLessonOccurrenceRead(LessonOccurrenceRead):
    """Occurrence в панели преподавателя — с полным списком участников."""

    participants: list[TeacherParticipantRead] = Field(default_factory=list)


class AttendanceActionRequest(BaseModel):
    action: Literal["joined", "declined"] = Field(
        ..., description="Ученик подтверждает явку или отказывается"
    )


# ─── Фаза 3 (tsk-430/435): панель преподавателя, перенос, ad-hoc ───────────


class TeacherAttendanceActionRequest(BaseModel):
    student_id: int = Field(..., description="Участник occurrence, чью явку правит преподаватель")
    action: Literal["manual_present", "manual_absent"] = Field(
        ..., description="Преподаватель вручную отмечает присутствие/отсутствие ученика"
    )


class AddStudentRequest(BaseModel):
    """Преподаватель добавляет ученика на занятие вручную (создаёт ad-hoc occurrence)."""

    teacher_id: int = Field(..., description="ID преподавателя (должен совпадать с вызывающим)")
    student_id: int = Field(..., description="ID ученика")
    scheduled_at: datetime = Field(..., description="Дата и время занятия (UTC)")
    duration_minutes: int = Field(..., gt=0, le=480)


class AddParticipantRequest(BaseModel):
    """Добавить ученика к УЖЕ существующему occurrence (например, подключить
    опоздавшего/новенького к уже идущей группе)."""

    student_id: int = Field(..., description="ID ученика")


class AdHocRequest(BaseModel):
    """Ученик сам записывается на отработку вне регулярного расписания."""

    teacher_id: int = Field(..., description="ID преподавателя")
    scheduled_at: datetime = Field(..., description="Дата и время занятия (UTC)")
    duration_minutes: int = Field(..., gt=0, le=480)


class RescheduleRequest(BaseModel):
    new_scheduled_at: datetime = Field(..., description="Новое время занятия (UTC)")


class AvailableSlotOption(BaseModel):
    scheduled_at: datetime
