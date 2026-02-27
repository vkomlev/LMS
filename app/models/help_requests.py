"""Модель заявок на помощь (Learning Engine V1, этап 3.8)."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.users import Users
    from app.models.tasks import Tasks
    from app.models.courses import Courses
    from app.models.attempts import Attempts
    from app.models.messages import Messages
    from app.models.help_request_replies import HelpRequestReplies


class HelpRequests(Base):
    """
    Заявки студентов на помощь по заданию (teacher help-requests, этап 3.8).
    """
    __tablename__ = "help_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE", name="help_requests_student_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], ondelete="CASCADE", name="help_requests_task_id_fkey"
        ),
        ForeignKeyConstraint(
            ["course_id"], ["courses.id"], ondelete="SET NULL", name="help_requests_course_id_fkey"
        ),
        ForeignKeyConstraint(
            ["attempt_id"], ["attempts.id"], ondelete="SET NULL", name="help_requests_attempt_id_fkey"
        ),
        ForeignKeyConstraint(
            ["event_id"], ["learning_events.id"], ondelete="SET NULL", name="help_requests_event_id_fkey"
        ),
        ForeignKeyConstraint(
            ["thread_id"], ["messages.id"], ondelete="SET NULL", name="help_requests_thread_id_fkey"
        ),
        ForeignKeyConstraint(
            ["assigned_teacher_id"], ["users.id"], ondelete="SET NULL",
            name="help_requests_assigned_teacher_id_fkey"
        ),
        ForeignKeyConstraint(
            ["closed_by"], ["users.id"], ondelete="SET NULL", name="help_requests_closed_by_fkey"
        ),
        PrimaryKeyConstraint("id", name="help_requests_pkey"),
        {"comment": "Заявки на помощь по заданию (Learning Engine V1, этап 3.8)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'open'"))
    request_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'manual_help'"),
        comment="manual_help | blocked_limit (этап 3.8.1)",
    )
    auto_created: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
        comment="Создана автоматически при BLOCKED_LIMIT (этап 3.8.1)",
    )
    context_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
        comment="Контекст заявки (attempts_used, trigger и т.д.)",
    )
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    course_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    attempt_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    event_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thread_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    assigned_teacher_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    resolution_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    replies: Mapped[List["HelpRequestReplies"]] = relationship(
        "HelpRequestReplies",
        back_populates="help_request",
        foreign_keys="HelpRequestReplies.request_id",
    )
