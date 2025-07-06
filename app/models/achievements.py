from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import Integer, String, Boolean, Text, UniqueConstraint, PrimaryKeyConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user_achievements import UserAchievements

class Achievements(Base):
    """
    Модуль достижений: хранит шаблоны достижений и условия их получения.
    """
    __tablename__ = "achievements"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="achievements_pkey"),
        UniqueConstraint("name", name="achievements_name_key"),
        {"comment": "Достижения пользователей"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="Уникальный ID достижения")
    name: Mapped[str] = mapped_column(String, nullable=False, comment="Название достижения")
    condition: Mapped[dict] = mapped_column(JSONB, nullable=False, comment="Условия получения (JSON)")
    description: Mapped[Optional[str]] = mapped_column(Text, comment="Описание достижения")
    badge_image_url: Mapped[Optional[str]] = mapped_column(String(512), comment="URL значка")
    reward_points: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), nullable=False, comment="Баллы за достижение"
    )
    is_recurring: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False, comment="Повторяемость"
    )

    user_achievements: Mapped[List["UserAchievements"]] = relationship(
        "UserAchievements", back_populates="achievement"
    )
