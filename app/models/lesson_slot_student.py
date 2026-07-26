from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LessonSlotStudent(Base):
    """
    Участник закреплённого группового слота (tsk-435, Календарь LMS).
    M2M ``lesson_slot`` ↔ ``users``. ``is_active=false`` — мягкое удаление
    (сохраняет историю occurrence, куда участник уже был добавлен).
    """

    __tablename__ = "lesson_slot_student"
    __table_args__ = (
        ForeignKeyConstraint(
            ["slot_id"], ["lesson_slot.id"], ondelete="CASCADE",
            name="lesson_slot_student_slot_id_fkey",
        ),
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_slot_student_student_id_fkey",
        ),
        ForeignKeyConstraint(
            ["added_by"], ["users.id"], ondelete="SET NULL",
            name="lesson_slot_student_added_by_fkey",
        ),
        PrimaryKeyConstraint("id", name="lesson_slot_student_pkey"),
        UniqueConstraint("slot_id", "student_id", name="uq_lesson_slot_student_slot_student"),
        {"comment": "Участники закреплённого группового слота (tsk-435)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="Мягкое удаление участника из слота — сохраняет историю occurrence",
    )
    added_by: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
