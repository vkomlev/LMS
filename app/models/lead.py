"""Лиды — мини-CRM кабинета маркетолога (tsk-506).

Лид существует ДО регистрации, поэтому `access_requests` здесь не подходит: там
`user_id` NOT NULL, то есть запрос роли уже зарегистрированным человеком. Связь с
`users` появляется позже отдельным действием — `linked_student_id`, nullable.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: Код канала «другое» — при нём приписка обязательна, иначе источник теряется.
LEAD_SOURCE_OTHER = "other"


class LeadSource(Base):
    """Справочник каналов привлечения.

    Справочник, а не свободный текст (решение оператора 2026-08-01): иначе через
    полгода в поле будут «авито», «Авито» и «avito», и посчитать канал станет нечем.
    """

    __tablename__ = "lead_source"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="lead_source_pkey"),
        UniqueConstraint("code", name="uq_lead_source_code"),
        {"comment": "Справочник каналов привлечения лидов (tsk-506)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(Text, nullable=False, comment="Машинный код канала")
    name: Mapped[str] = mapped_column(Text, nullable=False, comment="Название для человека")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )


class Lead(Base):
    """Лид: кто-то заинтересовался до того, как завёл учётную запись."""

    __tablename__ = "leads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id"], ["lead_source.id"], ondelete="RESTRICT",
            name="leads_source_id_fkey",
        ),
        ForeignKeyConstraint(
            ["linked_student_id"], ["users.id"], ondelete="SET NULL",
            name="leads_linked_student_id_fkey",
        ),
        ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="SET NULL",
            name="leads_created_by_fkey",
        ),
        ForeignKeyConstraint(
            ["guest_session_id"], ["guest_session.id"], ondelete="SET NULL",
            name="leads_guest_session_id_fkey",
        ),
        ForeignKeyConstraint(
            ["quiz_course_id"], ["courses.id"], ondelete="SET NULL",
            name="leads_quiz_course_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="leads_pkey"),
        {"comment": "Лиды — мини-CRM кабинета маркетолога (tsk-506)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_detail: Mapped[Optional[str]] = mapped_column(
        Text, comment="Приписка к каналу — обязательна при канале «другое»"
    )
    full_name: Mapped[Optional[str]] = mapped_column(Text, comment="Как зовут, если известно")
    contact: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="Ссылка/ник/телефон как есть — формат не валидируем, каналы разные",
    )
    note: Mapped[Optional[str]] = mapped_column(Text)
    linked_student_id: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Проставляется после регистрации — до неё лид ни с кем не связан"
    )
    created_by: Mapped[Optional[int]] = mapped_column(Integer)
    guest_session_id: Mapped[Optional[UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        comment="Гостевая сессия, из которой пришла заявка — связь квиза с лидом (tsk-053)",
    )
    quiz_course_id: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Курс-квиз, после которого оставлен контакт (tsk-053)"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
