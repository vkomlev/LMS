from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional, List

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    SmallInteger,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.courses import Courses
    from app.models.users import Users


class UserCourses(Base):
    """
    Связь пользователей с курсами (порядок и дата добавления).
    """
    __tablename__ = "user_courses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="user_courses_user_id_fkey",
        ),
        ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            ondelete="CASCADE",
            name="user_courses_course_id_fkey",
        ),
        PrimaryKeyConstraint(
            "user_id",
            "course_id",
            name="user_courses_pkey",  # ✅ PK по (user_id, course_id)
        ),
        {"comment": "Связь пользователей с курсами"},
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        comment="ID пользователя",
    )
    course_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        comment="Идентификатор курса",
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        comment="Когда добавлен",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="План курса активен (student_course_plan)",
    )
    order_number: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        comment=(
            "Порядковый номер. "
            "⚠️ ВАЖНО: Автоматически устанавливается и пересчитывается триггером БД "
            "(trg_set_user_course_order_number). "
            "Не дублировать логику в коде приложения! "
            "См. docs/database-triggers-contract.md"
        ),
    )

    user: Mapped["Users"] = relationship(
        "Users",
        back_populates="user_courses",
    )
    course: Mapped["Courses"] = relationship(
        "Courses",
        back_populates="user_courses",
    )
