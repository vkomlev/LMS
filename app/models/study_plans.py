from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List
from datetime import datetime
from sqlalchemy import (
    Integer,
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.study_plan_courses import StudyPlanCourses
    from app.models.users import Users

class StudyPlans(Base):
    """
    Учебные планы пользователей.
    """
    __tablename__ = "study_plans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            ondelete="CASCADE", name="study_plans_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="study_plans_pkey"),
        {"comment": "Учебные планы пользователей"},
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, comment="ID учебного плана"
    )
    user_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="ID пользователя"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        comment="Дата создания"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"),
        nullable=False,
        comment="Активен ли план"
    )
    

    user: Mapped["Users"] = relationship(
        "Users", back_populates="study_plans"
    )
    study_plan_courses: Mapped[List["StudyPlanCourses"]] = relationship(
        "StudyPlanCourses", back_populates="study_plan"
    )
