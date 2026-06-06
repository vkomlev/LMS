from __future__ import annotations

from typing import TYPE_CHECKING, Optional, List

from sqlalchemy import (
    Boolean,
    Integer,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.courses import Courses
    from app.models.difficulty_levels import DifficultyLevels
    from app.models.task_results import TaskResults


class Tasks(Base):
    """
    Задания курсов и правила их проверки.

    Порядок показа (``order_position``) управляется триггерами БД
    (``trg_set_task_order_position`` / ``trg_reorder_tasks_after_delete``).
    Подробности — docs/database-triggers-contract.md разделы 13-14.
    """
    __tablename__ = "tasks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            ondelete="CASCADE",
            name="assignments_course_id_fkey",
        ),
        ForeignKeyConstraint(
            ["difficulty_id"],
            ["difficulties.id"],
            ondelete="RESTRICT",
            name="tasks_difficulties_fk",
        ),
        PrimaryKeyConstraint("id", name="assignments_pkey"),
        {"comment": "Задания курсов"},
    )

    id: Mapped[int] = mapped_column(
        Integer,
        server_default=text("nextval('assignments_id_seq')"),
        primary_key=True,
        comment="ID задания",
    )

    # 🔽 НОВОЕ: устойчивый внешний ID для импорта
    external_uid: Mapped[Optional[str]] = mapped_column(
        Text,
        unique=True,
        nullable=True,
        comment="Внешний устойчивый идентификатор задания (для импорта)",
    )

    # 🔽 НОВОЕ: максимальный балл за задачу
    max_score: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Максимальный балл за задачу",
    )

    task_content: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        comment="Контент задания (JSON)",
    )
    course_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="ID курса",
    )
    difficulty_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="ID уровня сложности",
    )
    solution_rules: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        comment="Правила проверки решения",
    )
    max_attempts: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Лимит попыток (null => default на уровне сервиса, Learning Engine V1)",
    )
    time_limit_sec: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Лимит времени на попытку в секундах (Learning Engine V1)",
    )
    order_position: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Позиция в курсе (NULL = автоматически в конец)",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        server_default=text("true"),
        nullable=False,
        comment="Active flag for student-facing task routing",
    )
    requirement_level: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'required'"),
        nullable=False,
        comment="Content requirement level: skippable, recommended, required",
    )

    course: Mapped["Courses"] = relationship(
        "Courses",
        back_populates="tasks",
    )
    difficulty: Mapped["DifficultyLevels"] = relationship(
        "DifficultyLevels",
        back_populates="tasks",
    )
    task_results: Mapped[List["TaskResults"]] = relationship(
        "TaskResults",
        back_populates="task",
    )
