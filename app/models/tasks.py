from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List
from sqlalchemy import (
    Integer,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
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
    """
    __tablename__ = "tasks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id"], ["courses.id"],
            ondelete="CASCADE", name="assignments_course_id_fkey"
        ),
        ForeignKeyConstraint(
            ["difficulty_id"], ["difficulty_levels.id"],
            name="assignments_difficulty_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="assignments_pkey"),
        {"comment": "Задания курсов"},
    )

    id: Mapped[int] = mapped_column(
        Integer,
        server_default=text("nextval('assignments_id_seq')"),
        primary_key=True,
        comment="ID задания"
    )
    task_content: Mapped[dict] = mapped_column(
        JSONB, nullable=False, comment="Контент задания (JSON)"
    )
    course_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="ID курса"
    )
    difficulty_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="ID уровня сложности"
    )
    solution_rules: Mapped[Optional[dict]] = mapped_column(
        JSONB, comment="Правила проверки решения"
    )
    

    course: Mapped["Courses"] = relationship("Courses", back_populates="tasks")
    difficulty: Mapped["DifficultyLevels"] = relationship(
        "DifficultyLevels", back_populates="tasks"
    )
    task_results: Mapped[List["TaskResults"]] = relationship(
        "TaskResults", back_populates="task"
    )
