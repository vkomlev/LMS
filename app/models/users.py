from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional
from datetime import datetime
from sqlalchemy import (
    BigInteger, Integer, String, Text, DateTime,
    UniqueConstraint, PrimaryKeyConstraint, text
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.association_tables import t_user_roles, t_student_teacher_links

if TYPE_CHECKING:
    from app.models.messages import Messages
    from app.models.notifications import Notifications
    from app.models.roles import Roles
    from app.models.social_posts import SocialPosts
    from app.models.task_results import TaskResults
    from app.models.user_achievements import UserAchievements
    from app.models.access_requests import AccessRequests
    from app.models.user_courses import UserCourses
    from app.models.attempts import Attempts
    from app.models.identity_link import IdentityLink
    from app.models.user_session import UserSession


class Users(Base):
    """
    Пользователи системы. email и password_hash nullable после M1 (passwordless auth).
    """
    __tablename__ = "users"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="users_pkey"),
        # UniqueConstraint на email удалён M1; заменён partial unique index WHERE email IS NOT NULL
        {"comment": "Пользователи системы"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="ID пользователя")
    # nullable после M1 (passwordless users допустимы)
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True, comment="Email пользователя")
    password_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True, comment="Хэш пароля")
    full_name: Mapped[Optional[str]] = mapped_column(String, comment="Полное имя")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        comment="Дата регистрации"
    )
    tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment="Telegram ID")

    role: Mapped[List["Roles"]] = relationship(
        "Roles", secondary=t_user_roles, back_populates="user"
    )
    messages: Mapped[List["Messages"]] = relationship(
        "Messages", foreign_keys="[Messages.recipient_id]", back_populates="recipient"
    )
    messages_: Mapped[List["Messages"]] = relationship(
        "Messages", foreign_keys="[Messages.sender_id]", back_populates="sender"
    )
    notifications: Mapped[List["Notifications"]] = relationship(
        "Notifications",
        foreign_keys="[Notifications.modified_by]",
        back_populates="users",
    )
    # Y-4: inbox-уведомления, адресованные этому user
    inbox_messages: Mapped[List["Notifications"]] = relationship(
        "Notifications",
        foreign_keys="[Notifications.user_id]",
        back_populates="recipient",
    )
    social_posts: Mapped[List["SocialPosts"]] = relationship("SocialPosts", back_populates="user")

    access_requests: Mapped[List["AccessRequests"]] = relationship(
        "AccessRequests", back_populates="user")
    user_achievements: Mapped[List["UserAchievements"]] = relationship(
        "UserAchievements", back_populates="user"
    )
    task_results: Mapped[List["TaskResults"]] = relationship("TaskResults", back_populates="user")

    user_courses: Mapped[List["UserCourses"]] = relationship(
        "UserCourses",
        back_populates="user",
    )
    attempts: Mapped[List["Attempts"]] = relationship("Attempts", back_populates="user")
    identities: Mapped[List["IdentityLink"]] = relationship("IdentityLink", back_populates="user")
    sessions: Mapped[List["UserSession"]] = relationship("UserSession", back_populates="user")
    # Преподаватель → его студенты
    students: Mapped[List["Users"]] = relationship(
        "Users",
        secondary=t_student_teacher_links,
        primaryjoin=(
            id == t_student_teacher_links.c.teacher_id  # этот пользователь — преподаватель
        ),
        secondaryjoin=(
            id == t_student_teacher_links.c.student_id  # связанный пользователь — студент
        ),
        back_populates="teachers",
    )

    # Студент → его преподаватели
    teachers: Mapped[List["Users"]] = relationship(
        "Users",
        secondary=t_student_teacher_links,
        primaryjoin=(
            id == t_student_teacher_links.c.student_id  # этот пользователь — студент
        ),
        secondaryjoin=(
            id == t_student_teacher_links.c.teacher_id  # связанный пользователь — преподаватель
        ),
        back_populates="students",
    )