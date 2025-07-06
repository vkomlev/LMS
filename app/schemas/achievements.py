from __future__ import annotations
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict



class AchievementCreate(BaseModel):
    name: str
    condition: Dict[str, Any]
    description: Optional[str] = None
    badge_image_url: Optional[str] = None
    reward_points: int = Field(0, ge=0)
    is_recurring: bool = False


class AchievementUpdate(BaseModel):
    name: Optional[str] = None
    condition: Optional[Dict[str, Any]] = None
    description: Optional[str] = None
    badge_image_url: Optional[str] = None
    reward_points: Optional[int] = None
    is_recurring: Optional[bool] = None


class AchievementRead(BaseModel):
    id: int
    name: str
    condition: Dict[str, Any]
    description: Optional[str]
    badge_image_url: Optional[str]
    reward_points: int
    is_recurring: bool

    class Config:
        model_config = ConfigDict(from_attributes=True)

