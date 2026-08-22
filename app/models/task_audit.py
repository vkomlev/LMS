from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaskAudit(Base):
    """
    Append-only журнал изменений ``tasks.course_id`` / ``tasks.is_active`` (tsk-114)
    и правила проверки ``tasks.solution_rules`` (tsk-636).

    Наполняется автоматически триггерами ``trg_task_audit_update`` /
    ``trg_task_audit_delete`` на ``tasks`` — писать в эту таблицу из кода не
    нужно (и не получится: ``task_audit_no_modify`` запрещает UPDATE/DELETE
    строк, а INSERT приложение никогда не делает напрямую). Модель нужна
    только для чтения при расследовании инцидентов вроде tsk-113 — см.
    docs/ai/task-audit.md.
    """

    __tablename__ = "task_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="tasks.id на момент изменения. Без FK: запись должна пережить DELETE задания.",
    )
    external_uid: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Снимок tasks.external_uid на момент изменения",
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False, comment="'UPDATE' | 'DELETE'")
    old_course_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    new_course_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    old_is_active: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    new_is_active: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    old_answer_key: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment=(
            "tsk-636: выжимка solution_rules ДО изменения — эталон короткого ответа "
            "с шагами нормализации, варианты выбора, режим начисления, флаги ручной/"
            "гибридной проверки. Заполняет SQL-функция task_answer_key. NULL — правило "
            "этим UPDATE не менялось либо запись сделана до tsk-636."
        ),
    )
    new_answer_key: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment=(
            "tsk-636: та же выжимка ПОСЛЕ изменения. NULL — правило не менялось, "
            "запись до tsk-636 либо действие DELETE."
        ),
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        nullable=False,
    )
    changed_by: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="app.audit_actor на момент записи; NULL = источник не проставил себя",
    )
    db_role: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="current_user соединения на момент записи — заполняется всегда",
    )
