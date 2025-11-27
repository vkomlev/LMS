from __future__ import annotations
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum


class AccessLevel(str, Enum):
    self_guided = "self_guided"
    auto_check = "auto_check"
    manual_check = "manual_check"
    group_sessions = "group_sessions"
    personal_teacher = "personal_teacher"


class CourseCreate(BaseModel):
    title: str
    access_level: AccessLevel
    description: Optional[str] = None
    parent_course_id: Optional[int] = None
    is_required: bool = False


class CourseUpdate(BaseModel):
    title: Optional[str] = None
    access_level: Optional[AccessLevel] = None
    description: Optional[str] = None
    parent_course_id: Optional[int] = None
    is_required: Optional[bool] = None


class CourseRead(BaseModel):
    id: int
    title: str
    access_level: AccessLevel
    description: Optional[str]
    parent_course_id: Optional[int]
    created_at: datetime
    is_required: bool
    course_uid: str | None = None

    model_config = ConfigDict(from_attributes=True)
