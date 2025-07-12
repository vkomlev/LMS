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
from app.models.association_tables import t_user_roles

if TYPE_CHECKING:
    from app.models.messages import Messages
    from app.models.notifications import Notifications
    from app.models.roles import Roles
    from app.models.social_posts import SocialPosts
    from app.models.study_plans import StudyPlans
    from app.models.task_results import TaskResults
    from app.models.user_achievements import UserAchievements
    from app.models.access_requests import AccessRequests
    
class Users(Base):
    """
    Пользователи системы (связь с Telegram через tg_id).
    """
    __tablename__ = "users"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="users_pkey"),
        UniqueConstraint("email", name="users_email_key"),
        {"comment": "Пользователи системы"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="ID пользователя")
    email: Mapped[str] = mapped_column(String, nullable=False, comment="Email пользователя")
    password_hash: Mapped[str] = mapped_column(String, nullable=False, comment="Хэш пароля")
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
    notifications: Mapped[List["Notifications"]] = relationship("Notifications", back_populates="users")
    social_posts: Mapped[List["SocialPosts"]] = relationship("SocialPosts", back_populates="user")
    study_plans: Mapped[List["StudyPlans"]] = relationship("StudyPlans", back_populates="user")
    access_requests: Mapped[List["AccessRequests"]] = relationship(
        "AccessRequests", back_populates="user")
    user_achievements: Mapped[List["UserAchievements"]] = relationship(
        "UserAchievements", back_populates="user"
    )
    task_results: Mapped[List["TaskResults"]] = relationship("TaskResults", back_populates="user")
