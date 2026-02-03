from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    Enum,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.courses import Courses


class Materials(Base):
    """
    Учебные материалы, привязанные к курсам.
    Порядок показа (order_position) управляется триггерами БД.
    """
    __tablename__ = "materials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id"], ["courses.id"],
            ondelete="CASCADE", name="materials_course_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="materials_pkey"),
        UniqueConstraint(
            "course_id", "external_uid",
            name="uq_materials_course_external_uid",
        ),
        {"comment": "Учебные материалы"},
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, comment="Уникальный идентификатор материала"
    )
    course_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Идентификатор курса"
    )
    title: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="Заголовок материала"
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Описание/инструкции по использованию"
    )
    caption: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Подпись к материалу"
    )
    type: Mapped[str] = mapped_column(
        Enum(
            "text", "video", "audio", "image", "link", "pdf", "office_document",
            "script", "document",
            name="content_type",
        ),
        nullable=False,
        comment="Тип материала",
    )
    content: Mapped[dict] = mapped_column(
        JSONB, nullable=False, comment="Содержимое материала в формате JSON"
    )
    order_position: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="Позиция в курсе (NULL = автоматически в конец)"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), nullable=False, comment="Активен ли материал"
    )
    external_uid: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="Внешний идентификатор для импорта (уникален в паре с course_id)"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        comment="Дата создания",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        comment="Дата обновления",
    )

    course: Mapped["Courses"] = relationship(
        "Courses", back_populates="materials"
    )
