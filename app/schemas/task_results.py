from __future__ import annotations
from typing import Any, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class TaskResultCreate(BaseModel):
    score: int
    user_id: int
    task_id: int
    metrics: Optional[Any] = None
    count_retry: Optional[int] = 0


class TaskResultUpdate(BaseModel):
    score: Optional[int] = None
    metrics: Optional[Any] = None
    count_retry: Optional[int] = None


class TaskResultRead(BaseModel):
    id: int
    score: int
    user_id: int
    task_id: int
    submitted_at: datetime
    metrics: Optional[Any]
    count_retry: int
    received_at: datetime

    class Config:
        model_config = ConfigDict(from_attributes=True)