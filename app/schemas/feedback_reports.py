"""Схемы обращений о проблемах и идеях (tsk-303, Поток B)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

FeedbackReportType = Literal["bug", "content", "feature_idea"]


class FeedbackReportCreateRequest(BaseModel):
    """Тело POST /feedback-reports.

    Автор не передаётся: он берётся из сессии, иначе обращение можно было бы
    подписать чужим именем.
    """
    report_type: FeedbackReportType = Field(
        ...,
        description="bug — проблема системы; content — проблема контента; feature_idea — идея",
    )
    body: str = Field(..., min_length=1, max_length=4000, description="Описание")
    course_id: Optional[int] = Field(None, description="К какому курсу относится, если известно")
    material_id: Optional[int] = Field(None, description="К какому материалу, если известно")
    task_id: Optional[int] = Field(None, description="К какому заданию, если известно")

    @field_validator("body")
    @classmethod
    def _body_not_blank(cls, v: str) -> str:
        """Пробелы — это не описание.

        `min_length` строку из пробелов пропускает: в инбоксе появилась бы
        строка, которую нечего разбирать. Тот же класс, что закрыт на уровне
        БД (`ck_feedback_reports_body_not_blank`), но с внятной ошибкой вместо
        500 от нарушенного ограничения.
        """
        body = v.strip()
        if not body:
            raise ValueError("Описание не может быть пустым")
        return body


class FeedbackReportCreateResponse(BaseModel):
    """Ответ создания обращения."""
    report_id: int
    status: str = "open"
    created_at: datetime


class FeedbackReportItem(BaseModel):
    """Обращение в списке инбокса."""
    report_id: int
    report_type: str
    status: str
    author_id: Optional[int] = None
    author_name: Optional[str] = None
    body: str
    course_id: Optional[int] = None
    course_title: Optional[str] = None
    material_id: Optional[int] = None
    task_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None
    closed_by: Optional[int] = None
    resolution_comment: Optional[str] = None


class FeedbackReportListResponse(BaseModel):
    """Список обращений."""
    items: list[FeedbackReportItem] = Field(default_factory=list)
    total: int = 0
    scope: str = Field(
        "own",
        description="all — инбокс методиста/админа (все обращения); own — только свои",
    )


class FeedbackReportCloseRequest(BaseModel):
    """Тело закрытия обращения."""
    resolution_comment: Optional[str] = Field(None, max_length=2000)


class FeedbackReportCloseResponse(BaseModel):
    """Ответ закрытия обращения."""
    report_id: int
    status: str = "closed"
    closed_at: Optional[datetime] = None
    already_closed: bool = False
