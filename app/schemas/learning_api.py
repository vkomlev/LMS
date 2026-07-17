"""
Pydantic-схемы запросов и ответов Learning API (этап 3).

Эндпоинты: next-item, materials/complete, tasks/start-or-get-attempt,
tasks/state, request-help, teacher/task-limits/override.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ----- Next item -----

NextItemType = Literal[
    "material", "task", "none", "blocked_dependency", "blocked_limit"
]


class NextItemResponse(BaseModel):
    type: NextItemType
    course_id: Optional[int] = None
    root_course_id: Optional[int] = Field(
        None,
        description="Корневой курс дерева элемента (root). Отличается от course_id, "
        "если элемент в листовом подкурсе. SPW строит навигацию по корням (tsk-127).",
    )
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


# ----- Skip item -----

class LearningSkipRequest(BaseModel):
    student_id: int = Field(..., description="ID СЃС‚СѓРґРµРЅС‚Р°")


class LearningSkipResponse(BaseModel):
    ok: bool = True
    student_id: int
    kind: Literal["material", "task"]
    material_id: Optional[int] = None
    task_id: Optional[int] = None
    status: Literal["skipped"] = "skipped"
    skipped_at: datetime


# ----- Start or get attempt -----

class StartOrGetAttemptRequest(BaseModel):
    student_id: int = Field(..., description="ID студента")
    source_system: str = Field(default="learning_api", description="Источник")
    root_course_id: Optional[int] = Field(
        default=None,
        description=(
            "Корневой курс, которым ученик пришёл к заданию (tsk-264). Узел графа "
            "переиспользуется несколькими курсами, поэтому попытки считаются в "
            "границах корня: новый курс — свежие попытки. Клиент знает корень из "
            "дерева/URL. Если не передан — сервер определяет его сам, когда узел "
            "лежит ровно в одном активном курсе ученика."
        ),
    )


class StartOrGetAttemptResponse(BaseModel):
    attempt_id: int
    user_id: int
    course_id: Optional[int] = None
    root_course_id: Optional[int] = Field(
        default=None,
        description=(
            "Корневой курс, в границах которого считаются попытки (tsk-264). "
            "NULL — путь неизвестен: попытка не расходует лимит ни в одном курсе."
        ),
    )
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
    # tsk-222: сохранённый ответ ученика по последнему task_result. SPW показывает
    # его как «Мой ответ» (read-only) на пройденном/на-проверке/заблокированном
    # задании. Содержит только ответ ученика (StudentAnswer), эталон не раскрывается.
    last_answer_json: Optional[dict[str, Any]] = Field(
        default=None,
        description="Сохранённый ответ ученика (task_results.answer_json) последнего результата",
    )
    last_is_correct: Optional[bool] = Field(
        default=None,
        description="is_correct последнего результата (None до ручной проверки SA_COM/TA)",
    )
    last_checked_at: Optional[datetime] = Field(
        default=None,
        description="checked_at последнего результата (None = на проверке у учителя)",
    )
    # tsk-227: флаг обязательного вложения из solution_rules.requires_attachment.
    # Клиент (SPW/TG_LMS) по нему включает обязательную загрузку файла и блокирует
    # submit без вложения. Сервер — источник истины (форс на сдаче), это лишь UX-сигнал.
    requires_attachment: bool = Field(
        default=False,
        description=(
            "Требуется ли обязательное вложение для зачёта (solution_rules.requires_attachment, "
            "tsk-227). Клиент показывает обязательную загрузку файла."
        ),
    )


# ----- Request help -----

class RequestHelpRequest(BaseModel):
    student_id: int = Field(..., description="ID студента")
    message: Optional[str] = Field(default=None, max_length=2000)


class RequestHelpResponse(BaseModel):
    ok: bool = True
    event_id: int
    deduplicated: bool = False
    request_id: Optional[int] = Field(None, description="ID заявки в help_requests (этап 3.8, опционально)")


# ----- Hint events (этап 3.6) -----

HintType = Literal["text", "video"]
HintAction = Literal["open"]


class HintEventRequest(BaseModel):
    student_id: int = Field(..., description="ID студента")
    attempt_id: int = Field(..., description="ID попытки")
    hint_type: HintType = Field(..., description="Тип подсказки: text | video")
    hint_index: int = Field(..., ge=0, description="Индекс подсказки (0-based)")
    action: HintAction = Field("open", description="Действие (open; enum с возможностью расширения)")
    source: str = Field(..., description="Источник события, например student_execute")


class HintEventResponse(BaseModel):
    ok: bool = True
    deduplicated: bool = False
    event_id: int = Field(..., description="ID записи в learning_events")


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
