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


class LessonSlotTeacher(Base):
    """
    Преподаватель закреплённого слота — совместное ведение (tsk-443).
    M2M ``lesson_slot`` ↔ ``users``. ``lesson_slot.teacher_id`` остаётся
    "создателем/основным" преподавателем; реальный источник истины "кто
    ведёт" (для видимости занятия в кабинете преподавателя, проверки
    пересечений слотов) — эта таблица. ``is_active=false`` — мягкое
    удаление (сохраняет историю уже сгенерированных occurrence).
    """

    __tablename__ = "lesson_slot_teacher"
    __table_args__ = (
        ForeignKeyConstraint(
            ["slot_id"], ["lesson_slot.id"], ondelete="CASCADE",
            name="lesson_slot_teacher_slot_id_fkey",
        ),
        ForeignKeyConstraint(
            ["teacher_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_slot_teacher_teacher_id_fkey",
        ),
        ForeignKeyConstraint(
            ["added_by"], ["users.id"], ondelete="SET NULL",
            name="lesson_slot_teacher_added_by_fkey",
        ),
        PrimaryKeyConstraint("id", name="lesson_slot_teacher_pkey"),
        UniqueConstraint("slot_id", "teacher_id", name="uq_lesson_slot_teacher_slot_teacher"),
        {"comment": "Преподаватели закреплённого слота — совместное ведение (tsk-443)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    teacher_id: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="Мягкое удаление со-преподавателя — сохраняет историю occurrence",
    )
    added_by: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
