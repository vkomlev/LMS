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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LessonOccurrence(Base):
    """
    Конкретное занятие (tsk-435, Календарь LMS): сгенерировано из
    ``lesson_slot`` регулярным расписанием, либо ad-hoc отработка вне
    расписания (``slot_id IS NULL``). Групповое — участники и их явка
    (статус) живут в ``lesson_occurrence_participant``, не здесь (историческая
    ревизия tsk-428 держала одного ``student_id``+``status`` прямо на
    occurrence — не пережило встречу с реальными групповыми данными).
    """

    __tablename__ = "lesson_occurrence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["slot_id"], ["lesson_slot.id"], ondelete="SET NULL",
            name="lesson_occurrence_slot_id_fkey",
        ),
        ForeignKeyConstraint(
            ["teacher_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_occurrence_teacher_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="lesson_occurrence_pkey"),
        CheckConstraint(
            "duration_minutes > 0", name="lesson_occurrence_duration_positive_check"
        ),
        {"comment": "Конкретное занятие: из слота или ad-hoc отработка, групповое (tsk-435)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="ID occurrence")
    slot_id: Mapped[Optional[int]] = mapped_column(
        Integer, comment="NULL = ad-hoc отработка вне регулярного расписания"
    )
    teacher_id: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
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
