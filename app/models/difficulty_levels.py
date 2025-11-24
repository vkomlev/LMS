from __future__ import annotations

from typing import TYPE_CHECKING, List

from sqlalchemy import (
    Integer,
    String,
    UniqueConstraint,
    PrimaryKeyConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.tasks import Tasks


class DifficultyLevels(Base):
    """
    Уровни сложности заданий.

    Таблица: difficulties
    Поля:
      - code    — машинный код уровня ('Theory', 'Easy', 'Normal', 'Hard', 'Project')
      - name_ru — русское название уровня ('Теория', 'Легко', ...)
      - weight  — условная сложность (1..5)
    """
    __tablename__ = "difficulties"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="difficulties_pkey"),
        UniqueConstraint("code", name="difficulties_code_key"),
        {"comment": "Уровни сложности заданий"},
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        comment="ID уровня сложности",
    )

    code: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment="Код уровня сложности (например, 'Easy', 'Normal', 'Hard')",
    )

    name_ru: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment="Название уровня сложности на русском",
    )

    weight: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Вес уровня сложности (1..5)",
    )

    tasks: Mapped[List["Tasks"]] = relationship(
        "Tasks",
        back_populates="difficulty",
    )
