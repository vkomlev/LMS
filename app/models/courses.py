from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional
from datetime import datetime
from sqlalchemy import (
    Integer, String, Text, Boolean, DateTime,
    Enum, ForeignKeyConstraint, Index, PrimaryKeyConstraint, text
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.association_tables import t_course_dependencies

if TYPE_CHECKING:
    
    from app.models.materials import Materials
    from app.models.social_posts import SocialPosts
    from app.models.user_courses import UserCourses
    from app.models.tasks import Tasks

class Courses(Base):
    """
    Курсы системы обучения, могут быть иерархические (parent_course).
    """
    __tablename__ = "courses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["parent_course_id"], ["courses.id"],
            ondelete="SET NULL", name="courses_parent_course_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="courses_pkey"),
        Index("idx_courses_parent", "parent_course_id"),
        {"comment": "Курсы системы обучения"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="ID курса")
    title: Mapped[str] = mapped_column(String, nullable=False, comment="Название курса")
    access_level: Mapped[str] = mapped_column(
        Enum(
            "self_guided", "auto_check", "manual_check",
            "group_sessions", "personal_teacher",
            name="access_level_type"
        ),
        nullable=False,
        comment="Уровень доступа к курсу"
    )
    description: Mapped[Optional[str]] = mapped_column(Text, comment="Описание курса")
    parent_course_id: Mapped[Optional[int]] = mapped_column(
        Integer, comment="ID родительского курса"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        comment="Дата создания"
    )
    is_required: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False, comment="Обязательный курс"
    )
    course_uid: Mapped[str | None] = mapped_column(
        String,
        unique=True,
        nullable=True,  # можно сделать NOT NULL позже, когда все курсы получат коды
        comment="Код курса для импорта (course_uid, например 'COURSE-PY-01')",
    )

    parent_course: Mapped[Optional["Courses"]] = relationship(
        "Courses", remote_side=[id], back_populates="parent_course_reverse"
    )
    parent_course_reverse: Mapped[List["Courses"]] = relationship(
        "Courses", remote_side=[parent_course_id], back_populates="parent_course"
    )
    
    required_course: Mapped[List["Courses"]] = relationship(
        "Courses",
        secondary=t_course_dependencies,
        primaryjoin=id == t_course_dependencies.c.course_id,
        secondaryjoin=id == t_course_dependencies.c.required_course_id,
        back_populates="course"
    )
    course: Mapped[List["Courses"]] = relationship(
        "Courses",
        secondary=t_course_dependencies,
        primaryjoin=id == t_course_dependencies.c.required_course_id,
        secondaryjoin=id == t_course_dependencies.c.course_id,
        back_populates="required_course"
    )
    materials: Mapped[List["Materials"]] = relationship("Materials", back_populates="course")
    social_posts: Mapped[List["SocialPosts"]] = relationship("SocialPosts", back_populates="course")
    tasks: Mapped[List["Tasks"]] = relationship("Tasks", back_populates="course")
    user_courses: Mapped[List["UserCourses"]] = relationship(
        "UserCourses", back_populates="course"
    )
