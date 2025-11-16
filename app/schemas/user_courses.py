from __future__ import annotations
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class UserCourseCreate(BaseModel):
    user_id: int
    course_id: int
    order_number: Optional[int] = None


class UserCourseUpdate(BaseModel):
    order_number: Optional[int] = None


class UserCourseRead(BaseModel):
    user_id: int
    course_id: int
    added_at: datetime
    order_number: Optional[int]

    model_config = ConfigDict(from_attributes=True)
