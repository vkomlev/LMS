from __future__ import annotations
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class SocialPostCreate(BaseModel):
    user_id: int
    content: str
    course_id: Optional[int] = None


class SocialPostUpdate(BaseModel):
    content: Optional[str] = None
    course_id: Optional[int] = None


class SocialPostRead(BaseModel):
    id: int
    user_id: int
    content: str
    post_date: datetime
    course_id: Optional[int]

    class Config:
        model_config = ConfigDict(from_attributes=True)
