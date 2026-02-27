"""Схемы API заявок на помощь преподавателя (Learning Engine V1, этап 3.8)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


HelpRequestStatus = Literal["open", "closed"]
HelpRequestStatusFilter = Literal["open", "closed", "all"]


# ----- GET list -----

class HelpRequestListItem(BaseModel):
    """Элемент списка заявок."""
    request_id: int = Field(..., description="ID заявки")
    status: HelpRequestStatus
    student_id: int
    student_name: Optional[str] = None
    task_id: int
    task_title: Optional[str] = None
    course_id: Optional[int] = None
    course_title: Optional[str] = None
    attempt_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    thread_id: Optional[int] = None
    event_id: Optional[int] = None


class HelpRequestListResponse(BaseModel):
    """Ответ списка заявок."""
    items: list[HelpRequestListItem] = Field(default_factory=list)
    total: int = 0


# ----- GET detail -----

class HelpRequestReplyItem(BaseModel):
    """Элемент истории ответов."""
    reply_id: int
    teacher_id: int
    message_id: int
    body: str
    close_after_reply: bool = False
    created_at: datetime


class HelpRequestDetailResponse(HelpRequestListItem):
    """Карточка заявки (список + доп. поля и история)."""
    message: Optional[str] = None
    closed_at: Optional[datetime] = None
    closed_by: Optional[int] = None
    resolution_comment: Optional[str] = None
    history: list[HelpRequestReplyItem] = Field(default_factory=list, description="Ответы преподавателей")


# ----- POST close -----

class HelpRequestCloseRequest(BaseModel):
    """Тело запроса закрытия заявки."""
    closed_by: int = Field(..., description="ID пользователя, закрывающего заявку")
    resolution_comment: Optional[str] = Field(None, max_length=2000)


class HelpRequestCloseResponse(BaseModel):
    """Ответ закрытия заявки."""
    request_id: int
    status: Literal["closed"] = "closed"
    closed_at: Optional[datetime] = None
    updated_at: datetime
    already_closed: bool = False


# ----- POST reply -----

class HelpRequestReplyRequest(BaseModel):
    """Тело запроса ответа на заявку."""
    teacher_id: int = Field(..., description="ID преподавателя")
    message: str = Field(..., min_length=1, max_length=4000, description="Текст ответа студенту")
    close_after_reply: bool = Field(False, description="Закрыть заявку после отправки ответа")
    idempotency_key: Optional[str] = Field(None, max_length=128, description="Ключ идемпотентности")


class HelpRequestReplyResponse(BaseModel):
    """Ответ на заявку (reply)."""
    request_id: int
    message_id: int
    thread_id: Optional[int] = None
    request_status: HelpRequestStatus = "open"
    deduplicated: bool = False
