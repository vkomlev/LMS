from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class TaskCreate(BaseModel):
    """
    Схема создания задания.

    external_uid и max_score опциональны, чтобы не ломать существующие клиенты:
    - external_uid — устойчивый ID из внешней системы (для импорта),
    - max_score   — максимальный балл за задачу.
    """
    task_content: Any
    course_id: int
    difficulty_id: int
    solution_rules: Optional[Any] = None

    external_uid: Optional[str] = None
    max_score: Optional[int] = None


class TaskUpdate(BaseModel):
    """
    Схема обновления задания (полевая, все поля опциональны).
    """
    task_content: Optional[Any] = None
    course_id: Optional[int] = None
    difficulty_id: Optional[int] = None
    solution_rules: Optional[Any] = None

    external_uid: Optional[str] = None
    max_score: Optional[int] = None


class TaskRead(BaseModel):
    """
    Схема чтения задания.
    """
    id: int
    task_content: Any
    course_id: int
    difficulty_id: int
    solution_rules: Optional[Any]

    external_uid: Optional[str] = None
    max_score: Optional[int] = None

    class Config:
        model_config = ConfigDict(from_attributes=True)
