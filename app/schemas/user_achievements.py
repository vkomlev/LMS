from __future__ import annotations
from typing import Any, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class UserAchievementCreate(BaseModel):
    user_id: int
    achievement_id: int
    progress: Optional[Any] = None


class UserAchievementUpdate(BaseModel):
    progress: Optional[Any] = None


class UserAchievementRead(BaseModel):
    user_id: int
    achievement_id: int
    earned_at: datetime
    progress: Optional[Any]

    class Config:
        model_config = ConfigDict(from_attributes=True)
