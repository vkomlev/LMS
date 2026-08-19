from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StudentPresence(Base):
    """
    Живое присутствие ученика в кабинете (tsk-591) — ОДНА строка на ученика.

    Кабинет шлёт сюда пульс раз в две минуты, пока вкладка открыта и видима.
    История не хранится намеренно: она заняла бы десятки тысяч строк за одно
    занятие, а нужен только ответ на вопрос «жив ли он прямо сейчас».

    ``last_seen_at`` — вкладка открыта. ``last_interaction_at`` — ученик ещё и
    делал что-то руками (ввод, касание, прокрутка) за прошедший интервал. Без
    второго поля «читает материал» и «ушёл, не закрыв вкладку» неразличимы, и
    сигнал преподавателю начал бы врать на каждом длинном тексте.
    """

    __tablename__ = "student_presence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="student_presence_student_id_fkey",
        ),
        PrimaryKeyConstraint("student_id", name="student_presence_pkey"),
        CheckConstraint(
            "context IS NULL OR context IN ('task', 'material', 'course', 'other')",
            name="student_presence_context_check",
        ),
        {"comment": "Живое присутствие ученика в кабинете: одна строка на ученика (tsk-591)"},
    )

    student_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    last_interaction_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    course_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    material_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
