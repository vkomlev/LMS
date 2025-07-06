from __future__ import annotations
from typing import TYPE_CHECKING, Optional
from sqlalchemy import (
    Integer,
    String,
    Text,
    Enum,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.courses import Courses


class Materials(Base):
    """
    Учебные материалы, привязанные к курсам.
    """
    __tablename__ = "materials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id"], ["courses.id"],
            ondelete="CASCADE", name="materials_course_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="materials_pkey"),
        {"comment": "Учебные материалы"},
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, comment="Уникальный идентификатор материала"
    )
    type: Mapped[str] = mapped_column(
        Enum("text", "video", "link", "pdf", name="content_type"),
        nullable=False,
        comment="Тип материала"
    )
    content: Mapped[dict] = mapped_column(
        JSONB, nullable=False, comment="Содержимое материала в формате JSON"
    )
    order_position: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Позиция в курсе"
    )
    course_id: Mapped[int] = mapped_column(
        Integer, comment="Идентификатор курса"
    )

    course: Mapped["Courses"] = relationship(
        "Courses", back_populates="materials"
    )
