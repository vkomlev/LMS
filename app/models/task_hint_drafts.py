"""Модель черновиков текстовых подсказок (tsk-1220)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: Все допустимые статусы — повторяет CHECK миграции.
HINT_DRAFT_STATUSES = ("draft", "approved", "rejected", "skipped", "blocked")


class TaskHintDrafts(Base):
    """Черновик подсказки, собранный моделью из ответов преподавателей.

    До подтверждения человеком в `hints_text` задания не попадает.
    """

    __tablename__ = "task_hint_drafts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_reply_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, server_default=sql_text("'{}'")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=sql_text("'draft'"))
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    lint_flags: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    reviewed_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
