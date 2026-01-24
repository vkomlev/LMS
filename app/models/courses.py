from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional
from datetime import datetime
from sqlalchemy import (
    Integer, String, Text, Boolean, DateTime,
    Enum, ForeignKeyConstraint, Index, PrimaryKeyConstraint, text
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.association_tables import t_course_dependencies, t_course_parents

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
        PrimaryKeyConstraint("id", name="courses_pkey"),
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

    # Родители курса (многие-ко-многим через course_parents)
    parent_courses: Mapped[List["Courses"]] = relationship(
        "Courses",
        secondary=t_course_parents,
        primaryjoin=id == t_course_parents.c.course_id,
        secondaryjoin=id == t_course_parents.c.parent_course_id,
        back_populates="child_courses",
    )
    # Дети курса (обратная сторона связи)
    child_courses: Mapped[List["Courses"]] = relationship(
        "Courses",
        secondary=t_course_parents,
        primaryjoin=id == t_course_parents.c.parent_course_id,
        secondaryjoin=id == t_course_parents.c.course_id,
        back_populates="parent_courses"
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
    
    @property
    def parent_course_ids(self) -> List[int]:
        """Получить список ID родительских курсов."""
        # Проверяем, загружены ли parent_courses
        # Используем hasattr и проверку на None, чтобы избежать lazy loading
        try:
            # Проверяем, есть ли у объекта загруженные parent_courses
            if hasattr(self, '_sa_instance_state'):
                state = self._sa_instance_state
                if 'parent_courses' in state.unloaded:
                    # Если relationships не загружены, возвращаем пустой список
                    # Это предотвратит lazy loading в неправильном контексте
                    return []
            # Если parent_courses загружены или не определены, возвращаем список ID
            if self.parent_courses:
                return [p.id for p in self.parent_courses]
            return []
        except Exception:
            # В случае любой ошибки (например, lazy loading в неправильном контексте)
            # возвращаем пустой список
            return []