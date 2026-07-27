from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LessonOccurrenceTeacher(Base):
    """
    Преподаватель конкретного занятия — совместное ведение (tsk-443).
    M2M ``lesson_occurrence`` ↔ ``users``. Заполняется генератором из
    ``lesson_slot_teacher`` на каждый тик (как участники) — не переносится
    вручную. ``lesson_occurrence.teacher_id`` остаётся "основным"
    преподавателем для обратной совместимости.
    """

    __tablename__ = "lesson_occurrence_teacher"
    __table_args__ = (
        ForeignKeyConstraint(
            ["occurrence_id"], ["lesson_occurrence.id"], ondelete="CASCADE",
            name="lesson_occurrence_teacher_occurrence_id_fkey",
        ),
        ForeignKeyConstraint(
            ["teacher_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_occurrence_teacher_teacher_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="lesson_occurrence_teacher_pkey"),
        UniqueConstraint(
            "occurrence_id", "teacher_id", name="uq_lesson_occurrence_teacher_occurrence_teacher",
        ),
        {"comment": "Преподаватели конкретного занятия — совместное ведение (tsk-443)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurrence_id: Mapped[int] = mapped_column(Integer, nullable=False)
    teacher_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
