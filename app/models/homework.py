"""Модели домашней работы: выдача и её состав (tsk-741).

Отметки «выполнено» здесь нет намеренно — выполнение выводится из
`task_results` и `student_material_progress`, где живёт настоящая работа
ученика. Подробности решения — в миграции `tsk741_homework`.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    pass


class HomeworkAssignment(Base):
    """Выдача домашней работы ученику: кому, когда, к какому сроку, кем."""

    __tablename__ = "homework_assignment"
    __table_args__ = {"comment": "tsk-741: выдача домашней работы ученику"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        comment="Кому выдано",
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
        comment="Когда выдано",
    )
    due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="Срок: обычно начало следующего занятия ученика",
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False,
        comment="auto — расчёт по темпу и классу; teacher — выдал преподаватель",
    )
    issued_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        comment="Кто выдал; NULL у автоматической выдачи",
    )
    occurrence_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("lesson_occurrence.id", ondelete="SET NULL"), nullable=True,
        comment="Занятие, после которого выдано",
    )
    planned_volume: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="Норма формулы на момент выдачи (элементов)",
    )
    volume_details: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True,
        comment="Из чего сложилась норма: надо/факт/качество/класс/недель до экзамена",
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Выдача отменена преподавателем; NULL — действует",
    )
    note: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Комментарий преподавателя",
    )

    items: Mapped[List["HomeworkItem"]] = relationship(
        "HomeworkItem", back_populates="homework", cascade="all, delete-orphan",
    )


class HomeworkItem(Base):
    """Один элемент выдачи: задание или материал (теория тоже домашняя работа)."""

    __tablename__ = "homework_item"
    __table_args__ = {"comment": "tsk-741: состав домашней работы"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    homework_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("homework_assignment.id", ondelete="CASCADE"), nullable=False,
    )
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="task | material",
    )
    task_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True,
    )
    material_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("materials.id", ondelete="CASCADE"), nullable=True,
    )
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Порядок в выдаче — учебный",
    )

    homework: Mapped["HomeworkAssignment"] = relationship(
        "HomeworkAssignment", back_populates="items",
    )
