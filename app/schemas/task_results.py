from __future__ import annotations

from typing import Any, Optional
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TaskResultCreate(BaseModel):
    """
    Схема создания результата по задаче.

    Новые поля (attempt_id, answer_json, max_score, is_correct, source_system)
    делаем опциональными, чтобы не ломать существующие вызовы.
    """
    score: int
    user_id: int
    task_id: int
    metrics: Optional[Any] = None
    count_retry: Optional[int] = 0

    attempt_id: Optional[int] = None
    answer_json: Optional[Any] = None
    max_score: Optional[int] = None
    is_correct: Optional[bool] = None
    source_system: Optional[str] = "system"


class TaskResultUpdate(BaseModel):
    """
    Схема обновления результата по задаче.
    """
    score: Optional[int] = None
    metrics: Optional[Any] = None
    count_retry: Optional[int] = None

    attempt_id: Optional[int] = None
    answer_json: Optional[Any] = None
    max_score: Optional[int] = None
    is_correct: Optional[bool] = None
    checked_at: Optional[datetime] = None
    checked_by: Optional[int] = None
    source_system: Optional[str] = None


class TaskResultRead(BaseModel):
    """
    Схема чтения результата по задаче.
    """
    id: int
    score: int
    user_id: int
    task_id: int
    submitted_at: datetime
    metrics: Optional[Any]
    count_retry: int
    received_at: datetime

    attempt_id: Optional[int]
    answer_json: Optional[Any]
    max_score: Optional[int]
    is_correct: Optional[bool]
    checked_at: Optional[datetime]
    checked_by: Optional[int]
    source_system: str

    model_config = ConfigDict(from_attributes=True)
