from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LessonAbsenceFollowup(Base):
    """
    Отметка «про этот пропуск у ученика уже спросили» (tsk-743).

    Нужна ровно для того, чтобы список «спросите, почему пропустил» в плане
    занятия схлопывался. Без неё пропуск от 28.08 всплывал бы на каждом
    следующем занятии до конца года: система не знает, что разговор был.

    Статус участия (``lesson_occurrence_participant.status``) при этом не
    меняется — ``no_show`` остаётся фактом явки (деньги, посещаемость, нагон
    ДЗ tsk-741). Здесь хранится работа преподавателя, а не пересмотр факта.

    ``reason`` — код в одно нажатие; ``note`` — свободный текст, если кода
    мало. Оба необязательны: на уроке печатать некогда, и обязательная причина
    привела бы либо к пропуску шага, либо к мусору.
    """

    __tablename__ = "lesson_absence_followup"
    __table_args__ = (
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_absence_followup_student_id_fkey",
        ),
        ForeignKeyConstraint(
            ["occurrence_id"], ["lesson_occurrence.id"], ondelete="CASCADE",
            name="lesson_absence_followup_occurrence_id_fkey",
        ),
        ForeignKeyConstraint(
            ["asked_by"], ["users.id"], ondelete="SET NULL",
            name="lesson_absence_followup_asked_by_fkey",
        ),
        PrimaryKeyConstraint("id", name="lesson_absence_followup_pkey"),
        CheckConstraint(
            "reason IS NULL OR reason IN ('illness', 'forgot', 'busy', 'no_answer', 'other')",
            name="ck_lesson_absence_followup_reason",
        ),
        {"comment": "tsk-743: отметка «про этот пропуск у ученика уже спросили»"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    occurrence_id: Mapped[int] = mapped_column(Integer, nullable=False)
    asked_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    asked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
    )
    reason: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
