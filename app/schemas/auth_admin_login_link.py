"""Схема ответа admin-выдачи ссылки входа ученику (tsk-930).

Сырой токен виден только здесь — в базе (`magic_link.token_hash`) лежит
только хеш, как у обычного email magic-link и у `parent_access_links`.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AdminLoginLinkIssuedRead(BaseModel):
    student_id: int
    issued_by_user_id: int
    url: str
    expires_at: datetime
