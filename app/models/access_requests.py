# app/models/access_requests.py
from __future__ import annotations
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Integer,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    Sequence,
    Enum,
    DateTime,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM

from app.db.base import Base
from app.models.users import Users
from app.models.roles import Roles

# Объявляем PostgreSQL-тип ENUM (флаг статуса запроса)
access_request_flag = PG_ENUM(
    "completed",
    "rejected",
    "not_ready",
    name="access_request_flag",
    create_type=False,  # миграция создаст его отдельно
)

class AccessRequests(Base):
    """
    Запросы пользователей на получение определённых ролей в системе.
    """
    __tablename__ = "access_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            ondelete="CASCADE", name="access_requests_user_id_fkey"
        ),
        ForeignKeyConstraint(
            ["role_id"], ["roles.id"],
            ondelete="CASCADE", name="access_requests_role_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="access_requests_pkey"),
        {"comment": "Запросы пользователей на доступ/роли"},
    )

    id: Mapped[int] = mapped_column(
        Integer,
        Sequence("access_requests_id_seq"),
        primary_key=True,
        comment="Уникальный идентификатор запроса"
    )
    user_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="ID пользователя"
    )
    role_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="ID запрашиваемой роли"
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        comment="Время запроса"
    )
    flag: Mapped[str] = mapped_column(
        access_request_flag,
        server_default=text("'not_ready'"),
        nullable=False,
        comment="Статус запроса"
    )

    # связи (не обязательно, но удобно)
    user: Mapped[Users] = relationship("Users", back_populates="access_requests")
    role: Mapped[Roles] = relationship("Roles", back_populates="access_requests")
