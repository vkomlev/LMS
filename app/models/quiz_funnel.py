"""Воронка сайта: ветки входного квиза и гость ветки в ученическом боте (tsk-1139).

Входной квиз — обычный курс-квиз с одним вопросом «для кого подбираем». Каждый
его вариант ведёт в свой квиз-ветку; связка хранится здесь, а не в варианте
ответа: `TaskOption` лишние поля отбрасывает при разборе.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class QuizFunnelBranch(Base):
    """Квиз-ветка входного квиза и её настройки."""

    __tablename__ = "quiz_funnel_branch"

    quiz_course_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True
    )
    entry_course_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    #: Код ветки для ссылок `?for=` и меток: parent / ege / teen / adult.
    branch_code: Mapped[str] = mapped_column(Text, nullable=False)
    #: ID варианта ответа входного вопроса, который ведёт в эту ветку.
    entry_option_id: Mapped[str] = mapped_column(Text, nullable=False)
    #: Где лежит PDF-бонус ветки (выдаёт бот). NULL — бонуса нет, бот не предлагается.
    pdf_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: False — регистрация из итога закрыта (ветка «родитель» до текста согласия).
    registration_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
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
