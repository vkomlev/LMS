from __future__ import annotations

from datetime import datetime, time
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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

if TYPE_CHECKING:
    pass


class LessonSlot(Base):
    """
    Закреплённый повторяющийся слот пары ученик-преподаватель (tsk-428,
    Календарь LMS Фаза 1). Индивидуальный, не групповой — по требованию
    оператора (см. docs/specs/2026-07-26-plan-kalendar-lms.md).

    Деактивация (``is_active=false``) вместо удаления — сохраняет историю
    уже сгенерированных ``lesson_occurrence``, привязанных к слоту.
    """

    __tablename__ = "lesson_slot"
    __table_args__ = (
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_slot_student_id_fkey",
        ),
        ForeignKeyConstraint(
            ["teacher_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_slot_teacher_id_fkey",
        ),
        ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="SET NULL",
            name="lesson_slot_created_by_fkey",
        ),
        PrimaryKeyConstraint("id", name="lesson_slot_pkey"),
        CheckConstraint(
            "student_id <> teacher_id", name="lesson_slot_student_teacher_distinct_check"
        ),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="lesson_slot_weekday_check"),
        CheckConstraint("duration_minutes > 0", name="lesson_slot_duration_positive_check"),
        {"comment": "Закреплённый повторяющийся слот пары ученик-преподаватель (tsk-428)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="ID слота")
    student_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="ID ученика")
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
