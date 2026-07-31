from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LessonOccurrenceTeacher(Base):
    """
    Преподаватель конкретного занятия — совместное ведение (tsk-443).
    M2M ``lesson_occurrence`` ↔ ``users``. Заполняется генератором из
    ``lesson_slot_teacher`` на каждый тик (как участники).
    ``lesson_occurrence.teacher_id`` остаётся "основным" преподавателем
    для обратной совместимости.

    tsk-492 — разовые исключения на ОДНО занятие, поверх постоянного состава слота:

    * ``is_active=False`` — «на этом занятии не ведёт» (подмена: заболел, отпуск).
      Строка не удаляется намеренно: генератор вставляет состав слота через
      ``ON CONFLICT DO NOTHING``, поэтому удалённую строку он воссоздал бы на
      следующем тике, а существующую-погашенную не трогает. Удаление здесь
      физически не может быть постоянным — гашение может.
    * ``is_one_off=True`` — поставлен на это занятие вручную, а не из состава
      слота. Такие строки переживают снятие преподавателя со СЛОТА: разовое
      назначение — отдельное решение методиста, и оптовая чистка его не касается.
    """

    __tablename__ = "lesson_occurrence_teacher"
    __table_args__ = (
        ForeignKeyConstraint(
            ["occurrence_id"], ["lesson_occurrence.id"], ondelete="CASCADE",
            name="lesson_occurrence_teacher_occurrence_id_fkey",
        ),
        ForeignKeyConstraint(
            ["teacher_id"], ["users.id"], ondelete="CASCADE",
            name="lesson_occurrence_teacher_teacher_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="lesson_occurrence_teacher_pkey"),
        UniqueConstraint(
            "occurrence_id", "teacher_id", name="uq_lesson_occurrence_teacher_occurrence_teacher",
        ),
        {"comment": "Преподаватели конкретного занятия — совместное ведение (tsk-443)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurrence_id: Mapped[int] = mapped_column(Integer, nullable=False)
    teacher_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), nullable=False,
    )
    is_one_off: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False,
    )
