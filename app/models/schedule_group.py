from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ScheduleGroup(Base):
    """
    Группа расписания: аудитория + предмет (tsk-1124), например
    «Дети · Информатика», «Взрослые · Тестирование».

    У слота ровно одна группа, у ученика — одна или несколько
    (``user_schedule_group``). Ученик без явных групп считается членом группы
    с ``is_default`` — она одна на всю базу (частичный уникальный индекс).
    """

    __tablename__ = "schedule_group"
    __table_args__ = (
        CheckConstraint("audience IN ('kids', 'adults')", name="schedule_group_audience_check"),
        UniqueConstraint("name", name="schedule_group_name_key"),
        Index(
            "schedule_group_single_default_idx", "is_default",
            unique=True, postgresql_where=text("is_default"),
        ),
        {"comment": "Группа расписания: аудитория + предмет (tsk-1124)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="ID группы расписания")
    audience: Mapped[str] = mapped_column(Text, nullable=False, comment="kids | adults")
    subject: Mapped[str] = mapped_column(Text, nullable=False, comment="Предмет")
    name: Mapped[str] = mapped_column(Text, nullable=False, comment="Подпись в интерфейсе")
    pricing_group_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("pricing_group.id", ondelete="SET NULL", name="schedule_group_pricing_group_id_fkey"),
        comment="Подсказка тарифной группы; деньги по ней не двигаются автоматически",
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class UserScheduleGroup(Base):
    """Группа расписания ученика (tsk-1124). Нет строк — группа по умолчанию."""

    __tablename__ = "user_schedule_group"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "group_id", name="user_schedule_group_pkey"),
        Index("user_schedule_group_group_id_idx", "group_id"),
        {"comment": "Группы расписания ученика; нет строк — группа по умолчанию (tsk-1124)"},
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE", name="user_schedule_group_user_id_fkey")
    )
    group_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("schedule_group.id", ondelete="CASCADE", name="user_schedule_group_group_id_fkey"),
    )
    added_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL", name="user_schedule_group_added_by_fkey")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
