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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AttendanceEvent(Base):
    """
    Append-only журнал действий по посещаемости конкретного занятия
    (tsk-428, Календарь LMS Фаза 1) — как ``audit_event``, записи не
    изменяются и не удаляются приложением.

    Текущий статус на ``lesson_occurrence.status`` — денормализованная
    проекция последнего события, не пересчёт истории при каждом чтении.
    """

    __tablename__ = "attendance_event"
    __table_args__ = (
        ForeignKeyConstraint(
            ["occurrence_id"], ["lesson_occurrence.id"], ondelete="CASCADE",
            name="attendance_event_occurrence_id_fkey",
        ),
        ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="SET NULL",
            name="attendance_event_actor_user_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="attendance_event_pkey"),
        CheckConstraint(
            "action IN ('joined', 'declined', 'manual_present', 'manual_absent', "
            "'auto_no_show')",
            name="attendance_event_action_check",
        ),
        {"comment": "Append-only журнал действий по посещаемости occurrence (tsk-428)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="ID события")
    occurrence_id: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Кто совершил действие; NULL для auto_no_show (система)"
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
