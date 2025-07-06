from __future__ import annotations
from typing import TYPE_CHECKING, List
from sqlalchemy import Integer, String, UniqueConstraint, PrimaryKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.tasks import Tasks

class DifficultyLevels(Base):
    """
    Уровни сложности заданий.
    """
    __tablename__ = "difficulty_levels"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="difficulty_levels_pkey"),
        UniqueConstraint("weight", name="difficulty_levels_weight_key"),
        {"comment": "Уровни сложности заданий"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="ID уровня сложности")
    name: Mapped[str] = mapped_column(String, nullable=False, comment="Имя уровня")
    weight: Mapped[int] = mapped_column(Integer, nullable=False, comment="Вес для расчётов")

    tasks: Mapped[List["Tasks"]] = relationship("Tasks", back_populates="difficulty")
