from __future__ import annotations
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class StudyPlanCourseCreate(BaseModel):
    study_plan_id: int
    course_id: int
    order_number: Optional[int] = None


class StudyPlanCourseUpdate(BaseModel):
    order_number: Optional[int] = None


class StudyPlanCourseRead(BaseModel):
    study_plan_id: int
    course_id: int
    added_at: datetime
    order_number: Optional[int]

    class Config:
        model_config = ConfigDict(from_attributes=True)