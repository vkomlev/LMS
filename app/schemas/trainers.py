"""Схемы ручек интерактивных тренажёров `/api/v1/trainers/*` (tsk-1247)."""
from __future__ import annotations

import re
from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.trainer_scenario import MISCONCEPTION_CODE_PATTERN

#: Потолок событий в одном пакете (бриф Р3).
MAX_EVENTS_PER_BATCH = 200
#: Потолок ввода ученика в событии (бриф Р3: ввод консоли ≤ 64 символов).
MAX_ANSWER_LEN = 64

TrainerContext = Literal["student", "guest", "embed"]
TrainerEventType = Literal[
    "trainer_start", "trainer_step", "trainer_done", "checkpoint_answer", "embed_view",
]

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class TrainerRead(BaseModel):
    """Ответ `GET /trainers/{slug}`."""

    slug: str
    title: str
    scenario: dict[str, Any] = Field(..., description="Сценарий trainer-scenario/1 как опубликован")
    version: str = Field(..., description="Версия сценария — то же значение, что в заголовке ETag")


class TrainerSuggestion(BaseModel):
    """Предложение тренажёра после неверного ответа на задание узла."""

    slug: str
    title: str


class TrainerEvent(BaseModel):
    """Одно событие тренажёра.

    Незнакомые поля молча отбрасываются (не пишутся): клиент с очередью
    досылки (бриф 2A) не должен застревать на 422 из-за нового поля.
    """

    model_config = ConfigDict(extra="ignore")

    type: TrainerEventType
    level: Optional[Union[int, str]] = Field(default=None, description="id уровня сценария (L1…) или его номер")
    step: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$", description="id шага")
    iter: Optional[int] = Field(default=None, ge=1, le=1000, description="Номер витка цикла для шагов trace (README §1.5)")
    ok: Optional[bool] = None
    misconception: Optional[str] = Field(
        default=None, pattern=MISCONCEPTION_CODE_PATTERN,
        description="Код ошибки — только из словаря misconceptions сценария",
    )
    ms: Optional[int] = Field(default=None, ge=0, le=3_600_000, description="Время на шаг/уровень, мс")
    stars: Optional[int] = Field(default=None, ge=0, le=60)
    answer: Optional[str] = Field(default=None, max_length=MAX_ANSWER_LEN, description="Ввод ученика (≤ 64 символов); у встройки не пишется")
    parent_domain: Optional[str] = Field(default=None, max_length=253, description="Домен страницы со встройкой (только context=embed)")

    @field_validator("level")
    @classmethod
    def _level(cls, v: Optional[Union[int, str]]) -> Optional[Union[int, str]]:
        if isinstance(v, int) and not 0 <= v <= 100:
            raise ValueError("level: номер уровня 0…100")
        if isinstance(v, str) and not re.match(r"^[A-Za-z0-9_-]{1,32}$", v):
            raise ValueError("level: id уровня — латиница, цифры, _ и -, до 32 символов")
        return v

    @field_validator("parent_domain")
    @classmethod
    def _domain(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        host = v.strip().lower().rstrip(".")
        if not _DOMAIN_RE.match(host):
            raise ValueError("parent_domain: нужно доменное имя без схемы, пути и порта")
        return host


class TrainerEventsRequest(BaseModel):
    """Тело `POST /trainers/{slug}/events`."""

    context: TrainerContext
    events: List[TrainerEvent] = Field(..., min_length=1, max_length=MAX_EVENTS_PER_BATCH)

    @model_validator(mode="after")
    def _domain_only_embed(self) -> "TrainerEventsRequest":
        if self.context != "embed" and any(e.parent_domain for e in self.events):
            raise ValueError("parent_domain принимается только в context=embed")
        return self


class TrainerEventsResponse(BaseModel):
    """Ответ 202: сколько событий записано (у встройки — только счётчики)."""

    accepted: int = Field(..., ge=0)
