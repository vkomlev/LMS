"""Схемы API Teacher Next Modes (Learning Engine V1, этап 3.9): claim-next, release, workload."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


# ----- Help Request Claim Next -----

class HelpRequestClaimNextRequest(BaseModel):
    """Тело запроса «взять следующий help-request»."""
    teacher_id: int = Field(..., description="ID преподавателя")
    request_type: Literal["manual_help", "blocked_limit", "all"] = Field(
        "all",
        description="Тип заявки: manual_help | blocked_limit | all",
    )
    status: Literal["open"] = Field("open", description="На этом этапе только open")
    ttl_sec: int = Field(120, ge=30, le=600, description="Время жизни блокировки в секундах")
    idempotency_key: Optional[str] = Field(None, max_length=128, description="Ключ идемпотентности")
    course_id: Optional[int] = Field(None, description="Фильтр по курсу")


class HelpRequestClaimItem(BaseModel):
    """Элемент заявки в ответе claim-next (минимальный контекст)."""
    request_id: int
    status: str
    request_type: str
    student_id: int
    task_id: int
    course_id: Optional[int] = None
    created_at: datetime
    priority: int = 100
    due_at: Optional[datetime] = None
    is_overdue: bool = False


class HelpRequestClaimNextResponse(BaseModel):
    """Ответ claim-next для help-requests."""
    item: Optional[HelpRequestClaimItem] = None
    lock_token: Optional[str] = None
    lock_expires_at: Optional[datetime] = None
    empty: bool = Field(..., description="True, если нет доступного кейса")


# ----- Help Request Release -----

class HelpRequestReleaseRequest(BaseModel):
    """Тело запроса освобождения блокировки заявки."""
    teacher_id: int = Field(..., description="ID преподавателя")
    lock_token: str = Field(..., min_length=1, description="Токен блокировки")


class HelpRequestReleaseResponse(BaseModel):
    """Ответ release для help-request."""
    released: bool = Field(..., description="True, если блокировка снята; False при идемпотентном вызове (уже свободен)")


# ----- Review Claim Next -----

class ReviewClaimNextRequest(BaseModel):
    """Тело запроса «взять следующую проверку»."""
    teacher_id: int = Field(..., description="ID преподавателя")
    ttl_sec: int = Field(120, ge=30, le=600, description="Время жизни блокировки в секундах")
    idempotency_key: Optional[str] = Field(None, max_length=128, description="Ключ идемпотентности")
    course_id: Optional[int] = Field(None, description="Фильтр по курсу")
    user_id: Optional[int] = Field(None, description="Фильтр по ученику")


class ReviewClaimItem(BaseModel):
    """Элемент результата в ответе claim-next для проверок (TaskResult + минимальный контекст)."""
    id: int
    task_id: int
    user_id: int
    score: int
    submitted_at: datetime
    max_score: Optional[int] = None
    is_correct: Optional[bool] = None
    answer_json: Optional[Dict[str, Any]] = None
    task_title: Optional[str] = None
    user_name: Optional[str] = None
    course_id: Optional[int] = None


class ReviewClaimNextResponse(BaseModel):
    """Ответ claim-next для manual review."""
    item: Optional[ReviewClaimItem] = None
    lock_token: Optional[str] = None
    lock_expires_at: Optional[datetime] = None
    empty: bool = Field(..., description="True, если нет доступного кейса")


# ----- Review Release -----

class ReviewReleaseRequest(BaseModel):
    """Тело запроса освобождения блокировки проверки."""
    teacher_id: int = Field(..., description="ID преподавателя")
    lock_token: str = Field(..., min_length=1, description="Токен блокировки")


class ReviewReleaseResponse(BaseModel):
    """Ответ release для review."""
    released: bool = Field(..., description="True, если блокировка снята; False при идемпотентном вызове")


# ----- Workload -----

class TeacherWorkloadResponse(BaseModel):
    """Сводка нагрузки преподавателя для главного экрана."""
    open_help_requests_total: int = Field(0, description="Всего открытых заявок на помощь")
    open_blocked_limit_total: int = Field(0, description="Открытых заявок типа blocked_limit")
    open_manual_help_total: int = Field(0, description="Открытых заявок типа manual_help")
    pending_manual_reviews_total: int = Field(0, description="Результатов в ожидании ручной проверки")
    overdue_total: int = Field(0, description="Просроченных (due_at < now) открытых заявок")
