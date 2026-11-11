"""Ручные разблокировки жёсткой последовательности подкурсов (tsk-1500).

Пока запись на пару (корневой курс, ученик) есть, гейт `strict_progression`
для этого ученика молчит. Ставит и снимает методист (см.
`app/api/v1/methodist_progression.py`); аудит — в audit_event через
`log_event` на месте вызова.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProgressionOverride(Base):
    __tablename__ = "progression_overrides"

    __table_args__ = (
        PrimaryKeyConstraint("id", name="progression_overrides_pkey"),
        UniqueConstraint(
            "root_course_id", "user_id", name="uq_progression_overrides_pair"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    root_course_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        comment="Корневой курс с включённой жёсткой последовательностью",
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="Ученик в обходе правила",
    )
    created_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Кто разблокировал (методист/админ)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Когда поставлена разблокировка",
    )
