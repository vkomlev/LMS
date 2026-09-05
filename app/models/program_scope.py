"""Персональный объём программы подготовки (tsk-798).

Сколько программы ученик физически успевает до своего срока и, следовательно,
какую часть тренажёра ему выдавать. Подробности решения — в миграции
`tsk798_program_scope`.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StudentProgramScope(Base):
    """Что из программы помещается в срок конкретному ученику."""

    __tablename__ = "student_program_scope"
    __table_args__ = {
        "comment": "tsk-798: персональный объём программы подготовки под срок и темп"
    }

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        comment="Чей план",
    )
    program_kind: Mapped[str] = mapped_column(
        String(16), nullable=False,
        comment="ege | oge — программа подготовки, к которой относится план",
    )
    deadline: Mapped[date] = mapped_column(
        Date, nullable=False, comment="К какому дню программу нужно закончить",
    )
    planned_pace: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment=(
            "Недельный темп, на который рассчитан план: базовое ожидание школы "
            "либо фактический темп ученика, если он выше"
        ),
    )
    core_total: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="Несокращаемых элементов в программе (теория, номера, материалы)",
    )
    drill_total: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="Заданий тренажёра (EASY+NORMAL), подлежащих выборке",
    )
    drill_allowed: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="Сколько заданий тренажёра помещается в срок при этом темпе",
    )
    core_trimmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
        comment=(
            "Бюджета не хватило даже на ядро — программу пришлось резать по "
            "номерам ЕГЭ. Сигнал преподавателю, не тихое обрезание"
        ),
    )
    per_course: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
        comment=(
            "{course_id: порог выборки} — бюджет тренажёра, разложенный по "
            "подкурсам пропорционально их размеру. Только растёт"
        ),
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
    )
