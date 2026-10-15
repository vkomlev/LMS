"""Персональный уровень обязательности элемента для ученика (tsk-1361).

Уровень у задания/материала (`requirement_level`) — одна отметка на всех.
Эта таблица хранит отступления от неё для конкретного ученика вместе с
причиной: кто или какое правило решило и почему. Подробности — в миграции
`tsk1361_requirement_override`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StudentRequirementOverride(Base):
    """Ученик × (задание | материал | узел курса) → уровень + причина."""

    __tablename__ = "student_requirement_override"
    __table_args__ = {
        "comment": "tsk-1361: персональная обязательность элемента с причиной"
    }

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    target_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="task | material | course (узел — на поддерево)"
    )
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="required | recommended | skippable"
    )
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False, comment="rule | human")
    rule_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class StudentRequirementSync(Base):
    """Когда правила последний раз пересчитали переопределения ученика (фаза Б1)."""

    __tablename__ = "student_requirement_sync"
    __table_args__ = {
        "comment": "tsk-1361: момент последнего пересчёта переопределений ученика"
    }

    student_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
