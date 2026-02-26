from __future__ import annotations

from typing import TYPE_CHECKING, Optional
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.users import Users
    from app.models.courses import Courses
    from app.models.task_results import TaskResults


class Attempts(Base):
    """
    Попытки выполнения набора заданий пользователями.
    """
    __tablename__ = "attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="attempts_user_id_fkey",
        ),
        ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            ondelete="SET NULL",
            name="attempts_course_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="attempts_pkey"),
        Index("idx_attempts_user", "user_id"),
        Index("idx_attempts_course", "course_id"),
        Index("idx_attempts_created_at", "created_at"),
        {"comment": "Попытки прохождения заданий/тестов"},
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        server_default=text("nextval('attempts_id_seq')"),
        comment="ID попытки",
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="ID пользователя",
    )
    course_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="ID курса (если применимо)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        comment="Время создания попытки",
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Время завершения попытки",
    )
    source_system: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'system'"),
        comment="Источник системы, создавшей попытку",
    )
    meta: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Дополнительные метаданные (таймлимит, заголовок, план задач и т.п.)",
    )
    time_expired: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="Попытка завершена по таймауту (Learning Engine V1)",
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Время аннулирования попытки (Learning Engine V1, этап 3.5)",
    )
    cancel_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Причина аннулирования (опционально)",
    )

    user: Mapped["Users"] = relationship(
        "Users",
        back_populates="attempts",
    )
    course: Mapped[Optional["Courses"]] = relationship("Courses")
    task_results: Mapped[list["TaskResults"]] = relationship(
        "TaskResults",
        back_populates="attempt",
        cascade="all, delete-orphan",
    )
