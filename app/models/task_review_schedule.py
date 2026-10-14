"""Модель расписания повторов заданий (tsk-992, П3)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    UniqueConstraint,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaskReviewSchedule(Base):
    """Срок следующего повтора задания «сначала завалил, потом сдал».

    Одна строка на (ученик, задание, корневой курс). Стадия 0/1/2 — интервал
    3/7/21 день; после верного повтора на стадии 2 ставится `completed_at`.
    """

    __tablename__ = "task_review_schedule"
    __table_args__ = (
        CheckConstraint("interval_stage BETWEEN 0 AND 2", name="ck_task_review_schedule_stage"),
        UniqueConstraint(
            "student_id", "task_id", "course_id",
            name="uq_task_review_schedule_student_task_course",
        ),
        Index(
            "ix_task_review_schedule_due_open",
            "student_id", "course_id", "due_at",
            postgresql_where=sql_text("completed_at IS NULL"),
        ),
        {"comment": "Расписание повторов заданий «сначала завалил, потом сдал» (tsk-992)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        comment="Корневой курс, в контексте которого шло обучение",
    )
    interval_stage: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=sql_text("0"),
        comment="Стадия интервала: 0 → 3 дня, 1 → 7 дней, 2 → 21 день",
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_result_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("task_results.id", ondelete="SET NULL"),
        nullable=True,
        comment="Результат, по которому выставлен текущий срок",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Верный повтор на стадии 2 — задание закреплено",
    )
