"""Схемы лидов (tsk-506)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


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
    source_id: Optional[int] = None
    source_detail: Optional[str] = Field(default=None, max_length=500)
    full_name: Optional[str] = Field(default=None, max_length=300)
    contact: Optional[str] = Field(default=None, min_length=1, max_length=500)
    note: Optional[str] = None


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
