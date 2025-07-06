from __future__ import annotations
from typing import TYPE_CHECKING, Optional
from datetime import datetime
from sqlalchemy import (
    Integer,
    Text,
    Sequence,
    DateTime,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.users import Users


class Notifications(Base):
    """
    Шаблоны уведомлений и их версии.
    """
    __tablename__ = "notifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["modified_by"], ["users.id"],
            name="template_versions_modified_by_fkey"
        ),
        PrimaryKeyConstraint("id", name="template_versions_pkey"),
        {"comment": "Версии шаблонов уведомлений"},
    )

    id: Mapped[int] = mapped_column(
        Integer,
        Sequence("template_versions_id_seq"),
        primary_key=True,
        comment="ID версии уведомления"
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Текст шаблона"
    )
    modified_by: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Кто изменил шаблон"
    )
    modified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        comment="Время изменения"
    )

    users: Mapped[Optional["Users"]] = relationship(
        "Users", back_populates="notifications"
    )
