"""Воронка сайта: квиз с развилкой по правилам и гость в ученическом боте (tsk-1139).

Вопросы квиза — задания его курса (гостевые попытки пишутся как обычно), а
правила — ветки, условия показа, переходы, итоги со своими текстами — лежат
спецификацией в `quiz_funnel_spec`: `TaskOption` лишние поля отбрасывает.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class QuizFunnelSpec(Base):
    """Спецификация квиза-воронки (ветки, условия, итоги, тексты) — как в контенте."""

    __tablename__ = "quiz_funnel_spec"

    course_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True
    )
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class QuizFunnelProgress(Base):
    """Прохождение квиза-воронки гостем — источник замеров по веткам."""

    __tablename__ = "quiz_funnel_progress"

    guest_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("guest_session.id", ondelete="CASCADE"),
        primary_key=True,
    )
    course_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True
    )
    #: Параметры ссылки branch/role/dir, с которыми начали.
    params: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    branch: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    role: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    outcome_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class QuizFunnelBotLead(Base):
    """Гость ветки в боте: от выдачи ссылки до записи на пробное или отписки."""

    __tablename__ = "quiz_funnel_bot_lead"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: Параметр стартовой ссылки бота (без персональных данных).
    start_token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    guest_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("guest_session.id", ondelete="CASCADE"),
        nullable=False,
    )
    quiz_course_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    tg_username: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reminder_step: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    next_reminder_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    unsubscribed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    trial_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
