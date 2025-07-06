from __future__ import annotations
from typing import TYPE_CHECKING, Optional
from datetime import datetime
from sqlalchemy import (
    Integer,
    Text,
    DateTime,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    Index,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.courses import Courses
    from app.models.users import Users

class SocialPosts(Base):
    """
    Посты пользователей в социальной ленте системы.
    """
    __tablename__ = "social_posts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id"], ["courses.id"],
            name="social_posts_course_id_fkey"
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            ondelete="CASCADE", name="social_posts_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="social_posts_pkey"),
        Index("idx_social_posts_user", "user_id", "post_date"),
        {"comment": "Посты пользователей в социальной ленте"},
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, comment="ID поста"
    )
    user_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="ID пользователя"
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Текст поста"
    )
    post_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        comment="Дата публикации"
    )
    course_id: Mapped[Optional[int]] = mapped_column(
        Integer, comment="ID курса (опционально)"
    )
    
    course: Mapped[Optional["Courses"]] = relationship(
        "Courses", back_populates="social_posts"
    )
    user: Mapped["Users"] = relationship(
        "Users", back_populates="social_posts"
    )
