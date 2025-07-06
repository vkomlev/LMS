from __future__ import annotations
from sqlalchemy import (
    DateTime,
    Integer,
    SmallInteger,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    text,
)
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.courses import Courses
    from app.models.study_plans import StudyPlans

class StudyPlanCourses(Base):
    """
    Связь учебных планов с курсами (порядок и дата добавления).
    """
    __tablename__ = "study_plan_courses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["study_plan_id"], ["study_plans.id"],
            ondelete="CASCADE", name="study_plan_courses_study_plan_id_fkey"
        ),
        ForeignKeyConstraint(
            ["course_id"], ["courses.id"],
            ondelete="CASCADE", name="study_plan_courses_course_id_fkey"
        ),
        PrimaryKeyConstraint("study_plan_id", "course_id", name="study_plan_courses_pkey"),
        {"comment": "Связь учебных планов с курсами"},
    )

    study_plan_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, comment="ID учебного плана"
    )
    course_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, comment="ID курса"
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        comment="Когда добавлен"
    )
    order_number: Mapped[Optional[int]] = mapped_column(
        SmallInteger, comment="Порядковый номер"
    )
    
    study_plan: Mapped["StudyPlans"] = relationship(
        "StudyPlans", back_populates="study_plan_courses"
    )
    course: Mapped["Courses"] = relationship(
        "Courses", back_populates="study_plan_courses"
    )
