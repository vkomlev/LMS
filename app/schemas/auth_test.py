"""Схемы для тестовых auth-эндпоинтов (Phase Y-4 pre-S5).

Используются только в `app/api/v1/auth/test_session.py` под двойным gating
(`settings.env in {"dev","test"}` AND `settings.test_endpoints_enabled=True`).
В production — endpoint вернёт 404 до обработки body.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TestIssueSessionRequest(BaseModel):
    """Body для POST /auth/test/issue-session."""

    user_id: int = Field(..., gt=0, description="ID реального пользователя из users.id")


class TestIssueSessionResponse(BaseModel):
    """Ответ с подтверждением выдачи сессии (cookie в Set-Cookie header)."""

    user_id: int
    expires_at: datetime
    message: Literal["Test session issued"] = "Test session issued"
