from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LessonSlotStudent(Base):
    """
    Участник закреплённого группового слота (tsk-435, Календарь LMS).
    M2M ``lesson_slot`` ↔ ``users``. ``is_active=false`` — мягкое удаление
    (сохраняет историю occurrence, куда участник уже был добавлен).

    tsk-1442: у привязки свои даты действия (``active_from``/``active_until``,
    по МСК, включительно). Даты слота — это даты СЕТКИ, а не ученика: перевод
    28.10 на слот, заведённый 31.08, переписывал расчёту весь октябрь. Снятие
    закрывает привязку вчерашним днём, и расчёт месяца берёт для каждого дня
    ту привязку, что действовала в этот день. Поэтому строк на пару «слот +
    ученик» может быть несколько; уникальна только действующая.
    """

    __tablename__ = "lesson_slot_student"
    __table_args__ = (
        ForeignKeyConstraint(
            ["slot_id"], ["lesson_slot.id"], ondelete="CASCADE",
            name="lesson_slot_student_slot_id_fkey",
        ),
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_slot_student_student_id_fkey",
        ),
        ForeignKeyConstraint(
            ["added_by"], ["users.id"], ondelete="SET NULL",
            name="lesson_slot_student_added_by_fkey",
        ),
        PrimaryKeyConstraint("id", name="lesson_slot_student_pkey"),
        Index(
            "uq_lesson_slot_student_active",
            "slot_id",
            "student_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        {"comment": "Участники закреплённого группового слота (tsk-435)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="Мягкое удаление участника из слота — сохраняет историю occurrence",
    )
    active_from: Mapped[Optional[date]] = mapped_column(
        Date, comment="Первый день привязки (МСК); NULL — с начала, как у слота"
    )
    active_until: Mapped[Optional[date]] = mapped_column(
        Date, comment="Последний день привязки (МСК); NULL — открыта"
    )
    added_by: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
