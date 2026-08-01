from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ParentAccessLink(Base):
    """
    Ссылка доступа родителя к дашборду ученика без регистрации (tsk-498).

    Ссылка НЕ является сессией: токен открывает ровно один read-only эндпоинт
    дашборда конкретного ученика и не даёт войти в LMS под чьей-либо учёткой.
    В базе лежит только sha256-хеш — сырой токен возвращается один раз при
    создании (тот же приём, что у `magic_link`/`user_session`).

    Срока годности нет по решению оператора (2026-08-01): ссылка живёт, пока
    её не отозвали вручную (`revoked_at`).
    """

    __tablename__ = "parent_access_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="parent_access_links_student_id_fkey",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL",
            name="parent_access_links_created_by_user_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="parent_access_links_pkey"),
        UniqueConstraint("token_hash", name="uq_parent_access_links_token_hash"),
        {"comment": "Ссылки доступа родителя к дашборду ученика (tsk-498)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False, comment="sha256 сырого токена — сам токен не хранится"
    )
    student_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Чей дашборд открывает ссылка"
    )
    label: Mapped[Optional[str]] = mapped_column(
        Text, comment="Кому выдана — «мама», «папа»; только для оператора"
    )
    created_by_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Кто выдал ссылку (NULL — сервисный ключ)"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        comment="Когда отозвана. NULL — ссылка действует (срока годности нет)",
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), comment="Когда по ссылке последний раз открывали дашборд"
    )
