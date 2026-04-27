from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, Integer,
    LargeBinary, String, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.users import Users


class IdentityLink(Base):
    """Мультиidentity: привязка email / tg / vk к пользователю."""

    __tablename__ = "identity_link"
    __table_args__ = (
        UniqueConstraint("kind", "value", name="uq_identity_link_kind_value"),
        CheckConstraint("kind IN ('email', 'tg', 'vk')", name="identity_link_kind_check"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    vk_access_token_enc: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    vk_refresh_token_enc: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    vk_token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["Users"] = relationship("Users", back_populates="identities")
