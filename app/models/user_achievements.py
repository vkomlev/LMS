from __future__ import annotations
from typing import TYPE_CHECKING, Optional
from datetime import datetime
from sqlalchemy import (
    DateTime,
    Integer,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.achievements import Achievements
    from app.models.users import Users
    
class UserAchievements(Base):
    """
    Привязка пользователей к полученным достижениям.
    """
    __tablename__ = "user_achievements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["achievement_id"], ["achievements.id"],
            ondelete="CASCADE", name="user_achievements_achievement_id_fkey"
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            ondelete="CASCADE", name="user_achievements_user_id_fkey"
        ),
        PrimaryKeyConstraint("user_id", "achievement_id", name="user_achievements_pkey"),
        Index("idx_user_achievements", "user_id", "earned_at"),
        {"comment": "Связь пользователей с достижениями"},
    )

    user_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, comment="ID пользователя"
    )
    achievement_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, comment="ID достижения"
    )
    earned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        comment="Когда получено"
    )
    progress: Mapped[Optional[dict]] = mapped_column(
        JSONB, comment="Промежуточный прогресс (JSON)"
    )
    

    user: Mapped["Users"] = relationship("Users", back_populates="user_achievements")
    achievement: Mapped["Achievements"] = relationship(
        "Achievements", back_populates="user_achievements"
    )
