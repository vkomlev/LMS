"""Модель обращений о проблемах и идеях (tsk-303, Поток B)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
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


class FeedbackReports(Base):
    """
    Обращение о проблеме системы, проблеме контента или идея фичи.

    Второй поток единого инбокса преподавателя. С заявками помощи
    (`help_requests`) не пересекается: там всё построено вокруг пары
    (ученик, задание), которой здесь может не быть вовсе, и адресат другой —
    методист/админ, а не преподаватель конкретного ученика.
    """
    __tablename__ = "feedback_reports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["author_id"], ["users.id"], ondelete="SET NULL",
            name="feedback_reports_author_id_fkey",
        ),
        ForeignKeyConstraint(
            ["course_id"], ["courses.id"], ondelete="SET NULL",
            name="feedback_reports_course_id_fkey",
        ),
        ForeignKeyConstraint(
            ["material_id"], ["materials.id"], ondelete="SET NULL",
            name="feedback_reports_material_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], ondelete="SET NULL",
            name="feedback_reports_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["closed_by"], ["users.id"], ondelete="SET NULL",
            name="feedback_reports_closed_by_fkey",
        ),
        PrimaryKeyConstraint("id", name="feedback_reports_pkey"),
        {"comment": "tsk-303 Поток B: обращения о проблемах системы/контента и идеи фич"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_type: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="bug | content | feature_idea",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'open'")
    )
    author_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    course_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    material_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    resolution_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
