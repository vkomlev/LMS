"""Схемы лидов (tsk-506)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LeadSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    sort_order: int


class LeadRead(BaseModel):
    id: int
    source_id: int
    source_code: str
    source_name: str
    source_detail: Optional[str]
    full_name: Optional[str]
    contact: str
    note: Optional[str]
    linked_student_id: Optional[int]
    linked_student_name: Optional[str]
    created_at: datetime
    updated_at: datetime


class LeadCreateRequest(BaseModel):
    source_id: int
    source_detail: Optional[str] = Field(default=None, max_length=500)
    full_name: Optional[str] = Field(default=None, max_length=300)
    contact: str = Field(min_length=1, max_length=500)
    note: Optional[str] = None


class LeadUpdateRequest(BaseModel):
    """Правка лида. Присланное `null` стирает поле — кроме связи (tsk-518).

    `contact` — единственное, чем лид опознаётся до регистрации. Прислать его
    пустым или `null` нельзя: раньше такое доезжало до NOT NULL в базе и падало
    ошибкой сервера вместо понятного отказа. `min_length` от этого не спасал —
    он не срабатывает на `null` и на строку из пробелов.
    """

    source_id: Optional[int] = None
    source_detail: Optional[str] = Field(default=None, max_length=500)
    full_name: Optional[str] = Field(default=None, max_length=300)
    contact: Optional[str] = Field(default=None, min_length=1, max_length=500)
    note: Optional[str] = None

    @model_validator(mode="after")
    def _contact_stays_filled(self) -> "LeadUpdateRequest":
        if "contact" in self.model_fields_set and not (self.contact or "").strip():
            raise ValueError("Связь с лидом стереть нельзя — без неё его не найти")
        return self


class LeadLinkRequest(BaseModel):
    student_id: int


class StudentBrief(BaseModel):
    """Узкая карточка ученика для привязки лида.

    Намеренно без почты, телефона и ролей: маркетологу для привязки хватает
    имени и номера, а общий `/users/search` с полными персональными данными
    остаётся под прежним гейтом.
    """

    id: int
    full_name: Optional[str]
