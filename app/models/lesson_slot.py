from __future__ import annotations

from datetime import date, datetime, time
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    Time,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LessonSlot(Base):
    """
    Закреплённый повторяющийся слот преподавателя (tsk-435, Календарь LMS).
    Групповой: участники — в отдельной таблице ``lesson_slot_student``
    (историческая ревизия tsk-428 предполагала строго 1:1, реальные данные
    показали практику групповых занятий — см. план rework tsk-435).

    Деактивация (``is_active=false``) вместо удаления — сохраняет историю
    уже сгенерированных ``lesson_occurrence``, привязанных к слоту.
    """

    __tablename__ = "lesson_slot"
    __table_args__ = (
        ForeignKeyConstraint(
            ["teacher_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_slot_teacher_id_fkey",
        ),
        ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="SET NULL",
            name="lesson_slot_created_by_fkey",
        ),
        PrimaryKeyConstraint("id", name="lesson_slot_pkey"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="lesson_slot_weekday_check"),
        CheckConstraint("duration_minutes > 0", name="lesson_slot_duration_positive_check"),
        {"comment": "Закреплённый повторяющийся слот преподавателя, групповой (tsk-435)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="ID слота")
    teacher_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="ID преподавателя")
    weekday: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        comment="0=понедельник .. 6=воскресенье (Python date.weekday())",
    )
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    timezone: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'Europe/Moscow'"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="Деактивация вместо удаления — сохраняет историю occurrence",
    )
    active_until: Mapped[Optional[date]] = mapped_column(
        Date,
        comment=(
            "Последний день действия слота ВКЛЮЧИТЕЛЬНО; NULL — бессрочно. "
            "Смена расписания (tsk-679): старая сетка доживает август, новая "
            "начинается с сентября. Отличается от is_active=false тем, что "
            "слот ещё работает — просто до названного дня."
        ),
    )
    active_from: Mapped[Optional[date]] = mapped_column(
        Date,
        comment=(
            "Первый день действия слота ВКЛЮЧИТЕЛЬНО; NULL — действовал всегда. "
            "Парная к active_until (tsk-756): без неё слот, заведённый 31 августа "
            "под осеннюю сетку, считался действовавшим и весь август — то есть "
            "смена расписания переписывала прошлое."
        ),
    )
    created_by: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Admin/оператор, создавший слот"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
