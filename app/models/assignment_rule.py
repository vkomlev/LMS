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
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AssignmentRule(Base):
    """
    Правило автоматического назначения курса ученику (tsk-031).

    Событие (``trigger_event``) + условие (``condition``) → действие
    ``assign_course`` (назначить курс по ``target_course_uid``).
    Подробности модели — docs/ai/adr/0002-course-assignment-trigger-rules.md.
    """

    __tablename__ = "assignment_rule"
    __table_args__ = (
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], ondelete="CASCADE",
            name="assignment_rule_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["course_id"], ["courses.id"], ondelete="CASCADE",
            name="assignment_rule_course_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="assignment_rule_pkey"),
        UniqueConstraint("code", name="assignment_rule_code_key"),
        CheckConstraint(
            "trigger_event IN ('answer_value', 'task_failed', 'course_failed')",
            name="assignment_rule_trigger_event_check",
        ),
        CheckConstraint(
            "action_type IN ('assign_course')",
            name="assignment_rule_action_type_check",
        ),
        CheckConstraint(
            "refire_policy IN ('once_per_student', 'every_time')",
            name="assignment_rule_refire_policy_check",
        ),
        {"comment": "Правила автоматического назначения курсов (tsk-031)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="ID правила")
    code: Mapped[str] = mapped_column(Text, nullable=False, comment="Устойчивый код правила")
    title: Mapped[Optional[str]] = mapped_column(Text, comment="Описание для UI/админки")
    trigger_event: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="answer_value | task_failed | course_failed",
    )
    task_id: Mapped[Optional[int]] = mapped_column(Integer, comment="Отслеживаемая задача")
    course_id: Mapped[Optional[int]] = mapped_column(Integer, comment="Отслеживаемая тема=курс")
    condition: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="Параметры условия",
    )
    action_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'assign_course'"),
        comment="Тип действия",
    )
    target_course_uid: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Курс к назначению по course_uid (wp:<slug>)",
    )
    refire_policy: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'once_per_student'"),
        comment="once_per_student | every_time",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="Мягкое отключение правила",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
