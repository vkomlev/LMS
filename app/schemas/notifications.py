from __future__ import annotations
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class NotificationCreate(BaseModel):
    content: str
    modified_by: Optional[int] = None


class NotificationUpdate(BaseModel):
    content: Optional[str] = None
    modified_by: Optional[int] = None


class NotificationRead(BaseModel):
    id: int
    content: str
    modified_by: Optional[int]
    modified_at: datetime

    class Config:
        model_config = ConfigDict(from_attributes=True)
