"""Pydantic-схемы для embed-api эндпоинтов (Phase Y-5).

Display-only iframe: stem + KaTeX + CTA. Submit / interactive — нет.
Payload не содержит correct_answer / solution_rules.

См. tech-spec Y-5 §6.3 + §10.2.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class EmbedAuthIssueRequest(BaseModel):
    """Тело POST /embed-api/auth/issue."""

    course_uid: str = Field(..., description="course_uid публичного demo-курса")
    external_uid: str = Field(..., description="external_uid задачи")


class EmbedAuthIssueResponse(BaseModel):
    """Ответ POST /embed-api/auth/issue."""

    token: str = Field(..., description="Одноразовый JWT (HS256), TTL по конфигу")
    expires_at: datetime


class EmbedTaskOption(BaseModel):
    """Вариант ответа в embed-payload (display-only)."""

    id: str
    label: str = Field(..., description="Текст варианта (alias `text` → label)")


class EmbedTaskResponse(BaseModel):
    """Ответ GET /embed-api/courses/{course_uid}/task/{external_uid}.

    Whitelisted поля. SA_COM/TA не отдаются (404). correct_answer
    / solution_rules / max_attempts отсутствуют.
    """

    task_id: int
    external_uid: str
    course_uid: str
    type: Literal["SA", "SC", "MC"]
    stem: str
    options: Optional[List[EmbedTaskOption]] = None
    deeplink_url: str = Field(
        ...,
        description="Канонический URL для CTA «Решить в SPW» (target=_top)",
    )

    model_config = ConfigDict(from_attributes=False)
