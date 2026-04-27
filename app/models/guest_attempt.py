from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.guest_session import GuestSession


class GuestAttempt(Base):
    """Попытка гостевого пользователя. attributed_user_id заполняется при атрибуции."""

    __tablename__ = "guest_attempt"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    guest_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("guest_session.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    answer_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    is_correct: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    attributed_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    attributed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    guest_session: Mapped["GuestSession"] = relationship(
        "GuestSession", back_populates="guest_attempts"
    )
