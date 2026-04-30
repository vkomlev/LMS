"""Pydantic схемы для /me/notifications/* эндпоинтов (Phase Y-4)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class NotificationRead(BaseModel):
    """Inbox-запись пользователя для read-API."""

    id: int
    kind: str | None
    title: str | None
    content: str
    payload: dict[str, Any] | None
    created_at: datetime = Field(..., description="modified_at; время создания записи")
    read_at: datetime | None
    is_unread: bool


class UnreadCountResponse(BaseModel):
    """Ответ /me/notifications/unread-count."""

    count: int = Field(..., ge=0, description="Количество непрочитанных")
    last_check_at: datetime = Field(..., description="Серверное now() — для дебага клиента")


class MarkReadResponse(BaseModel):
    """Ответ POST /me/notifications/{id}/read."""

    id: int
    read_at: datetime
