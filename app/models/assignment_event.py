from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AssignmentEvent(Base):
    """
    Журнал назначений курсов (provenance + идемпотентность, tsk-031).

    Фиксирует факт назначения курса ученику: каким правилом (``rule_id``)
    или каким учителем (``assigned_by``), в каком контексте (попытка/результат).
    Само зачисление живёт в ``user_courses``; эта таблица — слой происхождения
    поверх него. См. docs/ai/adr/0002-course-assignment-trigger-rules.md.
    """

    __tablename__ = "assignment_event"
    __table_args__ = (
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="assignment_event_student_id_fkey",
        ),
        ForeignKeyConstraint(
            ["assigned_course_id"], ["courses.id"], ondelete="CASCADE",
            name="assignment_event_assigned_course_id_fkey",
        ),
        ForeignKeyConstraint(
            ["rule_id"], ["assignment_rule.id"], ondelete="SET NULL",
            name="assignment_event_rule_id_fkey",
        ),
        ForeignKeyConstraint(
            ["assigned_by"], ["users.id"], ondelete="SET NULL",
            name="assignment_event_assigned_by_fkey",
        ),
        ForeignKeyConstraint(
            ["attempt_id"], ["attempts.id"], ondelete="SET NULL",
            name="assignment_event_attempt_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="assignment_event_pkey"),
        CheckConstraint(
            "source IN ('auto_rule', 'manual_teacher')",
            name="assignment_event_source_check",
        ),
        {"comment": "Журнал назначений курсов (tsk-031)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="ID события")
    student_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="Кому назначено")
    assigned_course_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Что назначено"
    )
    rule_id: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Правило (NULL = ручное назначение)"
    )
    source: Mapped[str] = mapped_column(
        Text, nullable=False, comment="auto_rule | manual_teacher"
    )
    assigned_by: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Учитель (для ручного назначения)"
    )
    attempt_id: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Контекст срабатывания: попытка"
    )
    task_result_id: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Контекст срабатывания: результат задачи"
    )
    already_enrolled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="Ученик уже был на курсе на момент события",
    )
    detail: Mapped[Optional[dict]] = mapped_column(JSONB, comment="Доп. данные срабатывания")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
