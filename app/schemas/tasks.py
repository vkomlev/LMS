from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict


class TaskCreate(BaseModel):
    task_content: Any
    course_id: int
    difficulty_id: int
    solution_rules: Optional[Any] = None


class TaskUpdate(BaseModel):
    task_content: Optional[Any] = None
    course_id: Optional[int] = None
    difficulty_id: Optional[int] = None
    solution_rules: Optional[Any] = None


class TaskRead(BaseModel):
    id: int
    task_content: Any
    course_id: int
    difficulty_id: int
    solution_rules: Optional[Any]

    class Config:
        model_config = ConfigDict(from_attributes=True)
