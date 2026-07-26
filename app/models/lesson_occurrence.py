from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LessonOccurrence(Base):
    """
    Конкретное занятие (tsk-428, Календарь LMS Фаза 1): сгенерировано из
    ``lesson_slot`` регулярным расписанием, либо ad-hoc отработка вне
    расписания (``slot_id IS NULL``).

    ``status`` — денормализованная проекция последнего ``attendance_event``,
    не пересчитывается при каждом чтении (как ``audit_event`` — append-only
    журнал + денормализованное текущее состояние).
    """

    __tablename__ = "lesson_occurrence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["slot_id"], ["lesson_slot.id"], ondelete="SET NULL",
            name="lesson_occurrence_slot_id_fkey",
        ),
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_occurrence_student_id_fkey",
        ),
        ForeignKeyConstraint(
            ["teacher_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_occurrence_teacher_id_fkey",
        ),
        ForeignKeyConstraint(
            ["rescheduled_to_id"], ["lesson_occurrence.id"], ondelete="SET NULL",
            name="lesson_occurrence_rescheduled_to_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="lesson_occurrence_pkey"),
        CheckConstraint(
            "status IN ('scheduled', 'confirmed', 'declined', 'rescheduled', "
            "'no_show', 'completed')",
            name="lesson_occurrence_status_check",
        ),
        CheckConstraint(
            "duration_minutes > 0", name="lesson_occurrence_duration_positive_check"
        ),
        {"comment": "Конкретное занятие: из слота или ad-hoc отработка (tsk-428)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="ID occurrence")
    slot_id: Mapped[Optional[int]] = mapped_column(
        Integer, comment="NULL = ad-hoc отработка вне регулярного расписания"
    )
    student_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Денормализовано из слота — устойчиво к будущей деактивации слота",
    )
    teacher_id: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'scheduled'"),
    )
    rescheduled_to_id: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Цепочка переноса: занятие, на которое перенесено это"
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
