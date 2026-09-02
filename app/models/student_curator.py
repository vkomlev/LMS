"""Закрепление ученика за куратором — периоды ответственности (tsk-742).

Одна строка = один отрезок времени, в течение которого за ученика отвечал
конкретный преподаватель. Открытая строка (``ended_at IS NULL``) — действующий
куратор; закрытые — история. Отдельного «текущего» хранилища нет намеренно:
два источника правды разъезжаются в первый же день.

Почему это не ``student_teacher_links`` и не колонка в ``users`` — в миграции
``tsk742_student_curator``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StudentCurator(Base):
    """Период ответственности куратора за ученика."""

    __tablename__ = "student_curator"
    __table_args__ = {
        "comment": "tsk-742: периоды ответственности куратора за ученика (история)"
    }

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        comment="За кого отвечают",
    )
    curator_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        comment="Кто отвечает",
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
        comment="Начало периода ответственности",
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Конец периода; NULL — куратор действующий",
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False,
        comment="derived — выведено из расписания; manual — закрепил человек",
    )
    reason: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="Почему назначен ЭТОТ куратор",
    )
    assigned_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        # Даже у `source='derived'` здесь стоит человек: раскладку запускает
        # методист или админ кнопкой, а не фоновый проход. NULL остаётся для
        # запуска сервисным ключом — тогда называть автора было бы неправдой.
        comment="Кто закрепил: запустивший раскладку или закрепивший вручную; NULL — сервисный ключ",
    )
    ended_reason: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="Почему перестал быть куратором — отдельный смысл от reason",
    )
    ended_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        comment="Кто снял",
    )
