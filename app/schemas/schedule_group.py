"""Схемы групп расписания (tsk-1124)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Audience = Literal["kids", "adults"]


class ScheduleGroupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    audience: Audience
    subject: str
    name: str
    #: Подсказка тарифной группы; назначение группы ученику деньги не двигает.
    pricing_group_id: Optional[int] = None
    is_default: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ScheduleGroupCreate(BaseModel):
    audience: Audience
    subject: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=100, description="«Взрослые · Python»")
    pricing_group_id: Optional[int] = None


class ScheduleGroupUpdate(BaseModel):
    audience: Optional[Audience] = None
    subject: Optional[str] = Field(default=None, min_length=1, max_length=100)
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    pricing_group_id: Optional[int] = None
    #: Снять подсказку тарифа: `pricing_group_id: null` в частичной правке — «не меняем».
    clear_pricing_group: bool = False
    is_active: Optional[bool] = None


class StudentScheduleGroupsRead(BaseModel):
    student_id: int
    #: Явно назначенные группы; пусто — ученик в группе по умолчанию.
    group_ids: list[int]
    #: Группы, слоты которых ученик видит при записи и переносе.
    effective_group_ids: list[int]


class StudentScheduleGroupsUpdate(BaseModel):
    group_ids: list[int] = Field(
        default_factory=list,
        description="Полный набор групп ученика; пусто — вернуть в группу по умолчанию",
    )
