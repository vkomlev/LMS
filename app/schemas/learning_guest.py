"""Pydantic-схемы для guest-mode эндпоинтов (Phase Y-5).

См. tech-spec Y-5 §6.2 + §7.2.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.checking import StudentAnswer


class GuestSessionCreateResponse(BaseModel):
    """Ответ на POST /learning/guest/session."""

    guest_session_id: UUID = Field(..., description="UUID гостевой сессии")
    expires_at: datetime = Field(
        ...,
        description="Информационное время истечения cookie (now+30 дней). Физически TTL не enforced до cleanup post-MVP.",
    )

    model_config = ConfigDict(from_attributes=True)


class GuestCourseInfoResponse(BaseModel):
    """Ответ на GET /learning/guest/courses/{course_uid}."""

    course_uid: str
    title: str
    is_public_demo: bool = Field(..., description="Гарантированно True (404 если курс не demo)")


class GuestTaskOption(BaseModel):
    """Вариант ответа в guest-payload — без флагов is_correct/explanation."""

    id: str = Field(..., description="Устойчивый ID варианта (A/B/C/...)")
    text: str = Field(..., description="Текст варианта")


class GuestTaskResponse(BaseModel):
    """Ответ на GET /learning/guest/task/{task_id}.

    SA_COM в guest-mode исключены (404 — teacher review невозможна без user).
    `correct_answer` / `solution_rules` НЕ возвращаются.
    """

    task_id: int
    external_uid: Optional[str] = None
    course_id: int
    course_uid: Optional[str] = None
    type: Literal["SA", "SC", "MC"] = Field(..., description="Тип задачи; SA_COM/TA не отдаются гостям")
    stem: str
    options: Optional[List[GuestTaskOption]] = Field(
        default=None,
        description="Варианты для SC/MC; для SA не возвращается",
    )
    max_score: Optional[int] = None
    max_attempts: Optional[int] = None


class GuestAttemptCreateRequest(BaseModel):
    """Тело POST /learning/guest/attempts."""

    task_id: int = Field(..., description="ID задачи")
    answer: StudentAnswer = Field(..., description="Ответ гостя; разрешены только SA/SC/MC")


class GuestAttemptCreateResponse(BaseModel):
    """Ответ на POST /learning/guest/attempts."""

    attempt_id: int
    is_correct: bool
    score: int
    max_score: int


class AttributeGuestRequest(BaseModel):
    """Тело POST /me/attribute-guest."""

    guest_session_id: UUID


class AttributeGuestResponse(BaseModel):
    """Ответ на POST /me/attribute-guest."""

    guest_session_id: UUID
    attributed_count: int = Field(..., description="Сколько guest_attempt получили attributed_user_id (0 если уже атрибутирован)")
    already_attributed: bool = Field(..., description="True если guest_session уже принадлежал текущему user")
