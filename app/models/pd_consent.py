"""Журнал согласий на обработку персональных данных (tsk-1335, 152-ФЗ).

Только дозапись: строка — факт «кто, когда, на какую редакцию документов,
из какой формы согласился». Строки не правятся и не удаляются кодом
приложения; связи с лидом/пользователем/гостевой сессией при их удалении
обнуляются (SET NULL), сам факт согласия остаётся.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: Кто заполнил форму. `under_14` в журнал не попадает никогда — такой
#: запрос отклоняется до записи.
PD_FILLER_ROLES: tuple[str, ...] = ("parent", "adult", "teen_14_17")

#: Откуда пришло согласие.
PD_CONSENT_SOURCES: tuple[str, ...] = (
    "landing", "quiz_funnel", "quiz", "diagnostic", "magic_link", "vk", "tg", "integration",
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    """Текст CHECK-ограничения «значение из списка»."""
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class PdConsent(Base):
    """Одно согласие на обработку ПДн."""

    __tablename__ = "pd_consents"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pd_consents_pkey"),
        CheckConstraint(_in_list("filler_role", PD_FILLER_ROLES), name="ck_pd_consents_filler_role"),
        CheckConstraint(_in_list("source", PD_CONSENT_SOURCES), name="ck_pd_consents_source"),
        ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], ondelete="SET NULL", name="pd_consents_lead_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="SET NULL", name="pd_consents_user_id_fkey",
        ),
        ForeignKeyConstraint(
            ["guest_session_id"], ["guest_session.id"], ondelete="SET NULL",
            name="pd_consents_guest_session_id_fkey",
        ),
        Index("ix_pd_consents_user_id", "user_id"),
        Index("ix_pd_consents_lead_id", "lead_id"),
        {"comment": "Журнал согласий на обработку ПДн, только дозапись (tsk-1335)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    filler_role: Mapped[str] = mapped_column(Text, nullable=False)
    doc_consent_version: Mapped[str] = mapped_column(Text, nullable=False)
    doc_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[Optional[str]] = mapped_column(Text)
    lead_id: Mapped[Optional[int]] = mapped_column(Integer)
    user_id: Mapped[Optional[int]] = mapped_column(Integer)
    guest_session_id: Mapped[Optional[UUID]] = mapped_column(PgUUID(as_uuid=True))
    email: Mapped[Optional[str]] = mapped_column(
        Text, comment="Только для magic_link до создания пользователя"
    )
    ip: Mapped[Optional[str]] = mapped_column(INET)
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
