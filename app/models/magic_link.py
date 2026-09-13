from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKeyConstraint, Integer, LargeBinary, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MagicLink(Base):
    """Одноразовый magic-link для авторизации.

    Два происхождения токена, различённые тем, какое из полей `email`/
    `user_id` заполнено:
    - обычное письмо (`email` задан, `user_id` NULL) — TTL 15 мин, verify
      резолвит пользователя через email/identity_link (auto-create возможен);
    - admin-выдача (`user_id` задан, `email` может быть NULL) — TTL длиннее
      (tsk-930), verify резолвит пользователя НАПРЯМУЮ по `user_id`, минуя
      email/identity_link; `issued_by_user_id` — кто из персонала выдал.
    """

    __tablename__ = "magic_link"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
            name="magic_link_user_id_fkey",
        ),
        ForeignKeyConstraint(
            ["issued_by_user_id"], ["users.id"], ondelete="SET NULL",
            name="magic_link_issued_by_user_id_fkey",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
        comment="Admin-выдача напрямую по user_id, минуя email/identity_link (tsk-930)",
    )
    issued_by_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
        comment="Кто из персонала выдал ссылку вручную (admin); NULL — обычный email-flow",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
