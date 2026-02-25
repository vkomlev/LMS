"""
Pydantic-схемы запросов и ответов Learning API (этап 3).

Эндпоинты: next-item, materials/complete, tasks/start-or-get-attempt,
tasks/state, request-help, teacher/task-limits/override.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ----- Next item -----

NextItemType = Literal[
    "material", "task", "none", "blocked_dependency", "blocked_limit"
]


class NextItemResponse(BaseModel):
    type: NextItemType
    course_id: Optional[int] = None
    material_id: Optional[int] = None
    task_id: Optional[int] = None
    reason: Optional[str] = None
    dependency_course_id: Optional[int] = None


# ----- Material complete -----

class MaterialCompleteRequest(BaseModel):
    student_id: int = Field(..., description="ID студента")


class MaterialCompleteResponse(BaseModel):
    ok: bool = True
    student_id: int
    material_id: int
    status: Literal["completed"] = "completed"
    completed_at: Optional[datetime] = None


# ----- Start or get attempt -----

class StartOrGetAttemptRequest(BaseModel):
    student_id: int = Field(..., description="ID студента")
    source_system: str = Field(default="learning_api", description="Источник")


class StartOrGetAttemptResponse(BaseModel):
    attempt_id: int
    user_id: int
    course_id: Optional[int] = None
    created_at: datetime
    finished_at: Optional[datetime] = None
    source_system: str


# ----- Task state -----

TaskStateType = Literal[
    "OPEN", "IN_PROGRESS", "PASSED", "FAILED", "BLOCKED_LIMIT"
]


class TaskStateResponse(BaseModel):
    task_id: int
    student_id: int
    state: TaskStateType
    last_attempt_id: Optional[int] = None
    last_score: Optional[int] = None
    last_max_score: Optional[int] = None
    last_finished_at: Optional[datetime] = None
    attempts_used: int = 0
    attempts_limit_effective: int = 3


# ----- Request help -----

class RequestHelpRequest(BaseModel):
    student_id: int = Field(..., description="ID студента")
    message: Optional[str] = Field(default=None, max_length=2000)


class RequestHelpResponse(BaseModel):
    ok: bool = True
    event_id: int
    deduplicated: bool = False


# ----- Teacher override -----

class TaskLimitOverrideRequest(BaseModel):
    student_id: int = Field(..., description="ID студента")
    task_id: int = Field(..., description="ID задания")
    max_attempts_override: int = Field(..., gt=0, description="Лимит попыток")
    reason: Optional[str] = None
    updated_by: int = Field(..., description="ID учителя/методиста")


class TaskLimitOverrideResponse(BaseModel):
    ok: bool = True
    student_id: int
    task_id: int
    max_attempts_override: int
    updated_at: datetime
