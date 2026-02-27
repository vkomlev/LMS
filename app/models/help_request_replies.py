"""Модель ответов на заявки на помощь (Learning Engine V1, этап 3.8)."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.help_requests import HelpRequests
    from app.models.users import Users
    from app.models.messages import Messages


class HelpRequestReplies(Base):
    """
    Ответы преподавателя на заявку на помощь (идемпотентность по idempotency_key).
    """
    __tablename__ = "help_request_replies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["request_id"], ["help_requests.id"], ondelete="CASCADE",
            name="help_request_replies_request_id_fkey"
        ),
        ForeignKeyConstraint(
            ["teacher_id"], ["users.id"], ondelete="CASCADE",
            name="help_request_replies_teacher_id_fkey"
        ),
        ForeignKeyConstraint(
            ["message_id"], ["messages.id"], ondelete="CASCADE",
            name="help_request_replies_message_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="help_request_replies_pkey"),
        {"comment": "Ответы на заявки на помощь (Learning Engine V1, этап 3.8)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    teacher_id: Mapped[int] = mapped_column(Integer, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    close_after_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    help_request: Mapped["HelpRequests"] = relationship(
        "HelpRequests",
        back_populates="replies",
        foreign_keys=[request_id],
    )
