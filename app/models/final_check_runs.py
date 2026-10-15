"""Модель итогового среза после курса (tsk-994, П5)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FinalCheckRuns(Base):
    """Итоговый срез ученика по корневому курсу.

    Одна строка на (ученик, корневой курс): состав заданий выбирается один раз
    при первом открытии и дальше не меняется — повторное открытие показывает
    тот же набор. Итог (`result`) пишется, когда отвечены все задания.
    """

    __tablename__ = "final_check_runs"
    __table_args__ = (
        UniqueConstraint(
            "student_id", "course_id", name="uq_final_check_runs_student_course"
        ),
        {"comment": "Итоговый срез после курса: состав заданий и итог (tsk-994)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        comment="Корневой курс, по которому проходит срез",
    )
    task_ids: Mapped[list[int]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Состав среза — id заданий в порядке показа",
    )
    answers: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
        comment="Ответы среза: {task_id: {result_id, is_correct}}",
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Когда отвечено последнее задание среза",
    )
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Итог: доля верных, главы с ошибками, что поставлено на повтор",
    )
