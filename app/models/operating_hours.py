from __future__ import annotations

from datetime import datetime, time
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    Time,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OperatingHours(Base):
    """
    Часы работы школы, в рамках которых доступна гибкая отработка занятий
    вне закреплённого слота (tsk-428, Календарь LMS Фаза 1).

    MVP: одна конфигурация на всю школу, не per-teacher (см. спек
    docs/specs/2026-07-26-plan-kalendar-lms.md § Simplification Decisions).
    """

    __tablename__ = "operating_hours"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="operating_hours_pkey"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="operating_hours_weekday_check"),
        CheckConstraint("end_time > start_time", name="operating_hours_time_order_check"),
        {"comment": "Часы работы школы (tsk-428)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="ID записи")
    weekday: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        comment="0=понедельник .. 6=воскресенье (Python date.weekday())",
    )
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'Europe/Moscow'"),
        comment="IANA timezone; MVP — одна зона на всю школу",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
