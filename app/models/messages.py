from __future__ import annotations
from typing import Optional, TYPE_CHECKING
from datetime import datetime
from sqlalchemy import (
    Integer,
    String,
    DateTime,
    Boolean,
    Text,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.users import Users


class Messages(Base):
    """
    Сообщения между пользователями и системой (ботом).
    """
    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["recipient_id"], ["users.id"],
            ondelete="CASCADE", name="messages_recipient_id_fkey"
        ),
        ForeignKeyConstraint(
            ["sender_id"], ["users.id"],
            ondelete="SET NULL", name="messages_sender_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="messages_pkey"),
        Index("idx_messages_recipient", "recipient_id"),
        {"comment": "Сообщения между пользователями и преподавателями"},
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, comment="Идентификатор сообщения"
    )
    message_type: Mapped[str] = mapped_column(
        String, nullable=False, comment="Тип сообщения"
    )
    content: Mapped[dict] = mapped_column(
        JSONB, nullable=False, comment="Содержимое сообщения (JSON)"
    )
    sender_id: Mapped[Optional[int]] = mapped_column(
        Integer, comment="ID отправителя"
    )
    recipient_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="ID получателя"
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        comment="Время отправки"
    )
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        server_default=text("false"),
        nullable=False,
        comment="Прочитано?"
    )
    source_system: Mapped[str] = mapped_column(
        String(50),
        server_default=text("'system'"),
        nullable=False,
        comment="Источник сообщения"
    )
    
    recipient: Mapped["Users"] = relationship(
        "Users", foreign_keys=[recipient_id], back_populates="messages"
    )
    sender: Mapped[Optional["Users"]] = relationship(
        "Users", foreign_keys=[sender_id], back_populates="messages_"
    )
