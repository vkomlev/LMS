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


class ExternalLeadCreateRequest(BaseModel):
    """Заявка на лида от соседней системы (tsk-718).

    `external_source` + `external_id` — ключ склейки: повторный вызов с той же
    парой возвращает уже заведённого лида, а не создаёт второго. Обе строки
    обязательны и непустые именно поэтому.
    """

    external_source: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=128)
    source_code: str = Field(min_length=1, max_length=64)
    contact: str = Field(min_length=1, max_length=500)
    full_name: Optional[str] = Field(default=None, max_length=300)
    source_detail: Optional[str] = Field(default=None, max_length=500)
    note: Optional[str] = None

    @model_validator(mode="after")
    def _key_is_really_filled(self) -> "ExternalLeadCreateRequest":
        """Ключ склейки не бывает из пробелов.

        `min_length` строку из пробелов пропускает: она непустая. Но лид,
        заведённый на ключ «   », склеится с любым другим таким же и ни с чем
        осмысленным — то есть дедуп перестанет работать молча, ровно как на
        пустом значении. Поэтому пробельные строки отбиваются на входе, а сами
        значения приходят к базе уже подрезанными.
        """
        self.external_source = self.external_source.strip()
        self.external_id = self.external_id.strip()
        self.source_code = self.source_code.strip()
        self.contact = self.contact.strip()
        if not (self.external_source and self.external_id and self.source_code):
            raise ValueError(
                "Источник, внешний номер и код канала не могут быть пустыми"
            )
        if not self.contact:
            raise ValueError("Связь с лидом обязательна — без неё его не найти")
        return self


class ExternalLeadResponse(BaseModel):
    """Ответ служебного входа: номер лида и создан ли он именно сейчас."""

    lead_id: int
    created: bool


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
