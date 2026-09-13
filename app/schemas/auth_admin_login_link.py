"""Схемы admin-ручек ученика (tsk-930, tsk-931).

Сырой токен виден только здесь — в базе (`magic_link.token_hash`) лежит
только хеш, как у обычного email magic-link и у `parent_access_links`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AdminLoginLinkIssuedRead(BaseModel):
    student_id: int
    issued_by_user_id: int
    url: str
    expires_at: datetime


class AdminCreateStudentRequest(BaseModel):
    """tsk-931: свободная заметка оператора — не ФИО, уходит только в audit."""

    note: Optional[str] = Field(default=None, max_length=500)


class AdminStudentCreatedRead(BaseModel):
    id: int
    created_at: datetime
