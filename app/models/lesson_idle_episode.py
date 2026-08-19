from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LessonIdleEpisode(Base):
    """
    Простой ученика во время занятия (tsk-591): один эпизод на «затих →
    вернулся», а не запись на каждый проход фонового тика.

    ``kind``:

    * ``away`` — пульса из кабинета нет: ученик закрыл вкладку или ушёл;
    * ``idle`` — кабинет открыт, но ученик ничего не делает.

    Ровно то различение, которое просил оператор («вне системы» против
    «открыл задание и молчит»).

    Незакрытый эпизод у пары (занятие, ученик) может быть только один —
    это держит частичный уникальный индекс ``uq_lesson_idle_episode_open``,
    а не проверка в коде: два тика в разных процессах могли бы разойтись
    между SELECT и INSERT.
    """

    __tablename__ = "lesson_idle_episode"
    __table_args__ = (
        ForeignKeyConstraint(
            ["occurrence_id"], ["lesson_occurrence.id"], ondelete="CASCADE",
            name="lesson_idle_episode_occurrence_id_fkey",
        ),
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_idle_episode_student_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="lesson_idle_episode_pkey"),
        CheckConstraint("kind IN ('away', 'idle')", name="lesson_idle_episode_kind_check"),
        {"comment": "Простой ученика на занятии: один эпизод на «затих → вернулся» (tsk-591)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurrence_id: Mapped[int] = mapped_column(Integer, nullable=False)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    silent_since: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    material_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    course_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
