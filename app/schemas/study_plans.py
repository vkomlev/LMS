from __future__ import annotations
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class StudyPlanCreate(BaseModel):
    user_id: int
    is_active: Optional[bool] = True


class StudyPlanUpdate(BaseModel):
    is_active: Optional[bool] = None


class StudyPlanRead(BaseModel):
    id: int
    user_id: int
    created_at: datetime
    is_active: bool

    class Config:
        model_config = ConfigDict(from_attributes=True)
