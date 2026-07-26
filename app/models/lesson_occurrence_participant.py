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
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LessonOccurrenceParticipant(Base):
    """
    Явка ОДНОГО участника ОДНОГО occurrence (tsk-435, Календарь LMS).
    Групповое occurrence имеет несколько таких строк — у каждого участника
    свой независимый статус (подтвердил/отказался/не пришёл/...), отказ или
    перенос одного не затрагивает остальных участников той же группы.
    """

    __tablename__ = "lesson_occurrence_participant"
    __table_args__ = (
        ForeignKeyConstraint(
            ["occurrence_id"], ["lesson_occurrence.id"], ondelete="CASCADE",
            name="lesson_occurrence_participant_occurrence_id_fkey",
        ),
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_occurrence_participant_student_id_fkey",
        ),
        ForeignKeyConstraint(
            ["rescheduled_to_occurrence_id"], ["lesson_occurrence.id"], ondelete="SET NULL",
            name="lesson_occurrence_participant_rescheduled_to_fkey",
        ),
        PrimaryKeyConstraint("id", name="lesson_occurrence_participant_pkey"),
        UniqueConstraint(
            "occurrence_id", "student_id", name="uq_lesson_occurrence_participant_occ_student"
        ),
        CheckConstraint(
            "status IN ('scheduled', 'confirmed', 'declined', 'rescheduled', "
            "'no_show', 'completed')",
            name="lesson_occurrence_participant_status_check",
        ),
        {"comment": "Явка по каждому участнику occurrence независимо (tsk-435)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurrence_id: Mapped[int] = mapped_column(Integer, nullable=False)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'scheduled'"),
    )
    rescheduled_to_occurrence_id: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Новое occurrence, на которое этот участник перенёс явку"
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
