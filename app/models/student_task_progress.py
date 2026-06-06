from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StudentTaskProgress(Base):
    """Student-level task progress markers outside task_results."""

    __tablename__ = "student_task_progress"
    __table_args__ = (
        ForeignKeyConstraint(
            ["student_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="student_task_progress_student_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="CASCADE",
            name="student_task_progress_task_id_fkey",
        ),
        PrimaryKeyConstraint("student_id", "task_id", name="student_task_progress_pkey"),
    )

    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'skipped'"),
        nullable=False,
    )
    skipped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
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
