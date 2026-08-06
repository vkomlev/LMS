"""Модель истории возвратов заявки на помощь (tsk-303, лестница помощи)."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.help_requests import HelpRequests
    from app.models.users import Users


class HelpRequestReopens(Base):
    """
    Возврат заявки учеником («всё равно ничего не понял», уровень 1 → повтор).

    Одна строка — один возврат. Счётчик возвратов не хранится полем на заявке:
    он выводится ``COUNT(*)`` отсюда, поэтому второго источника правды нет.
    ``teacher_id`` — тот, чей ответ не помог (KPI преподавателя), а не всегда
    ``assigned_teacher_id``: к заявке по ACL может ответить и методист, и
    преподаватель по связи с учеником.
    """
    __tablename__ = "help_request_reopens"
    __table_args__ = (
        ForeignKeyConstraint(
            ["request_id"], ["help_requests.id"], ondelete="CASCADE",
            name="help_request_reopens_request_id_fkey",
        ),
        ForeignKeyConstraint(
            ["teacher_id"], ["users.id"], ondelete="SET NULL",
            name="help_request_reopens_teacher_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="help_request_reopens_pkey"),
        {"comment": "tsk-303: история возвратов заявки помощи учеником (KPI преподавателя)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    teacher_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reopened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    help_request: Mapped["HelpRequests"] = relationship(
        "HelpRequests",
        back_populates="reopens",
        foreign_keys=[request_id],
    )
