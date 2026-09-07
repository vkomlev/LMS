from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaskResultAudit(Base):
    """
    Append-only журнал изменений оценки в ``task_results`` (tsk-803):
    ``score`` и ``is_correct`` до и после каждого UPDATE.

    Наполняется триггерами ``trg_task_result_audit_update`` и
    ``trg_task_result_audit_delete`` на ``task_results`` (общая функция
    ``log_task_result_audit``) — писать в эту таблицу из кода не нужно и не
    получится: ``task_result_audit_no_modify`` запрещает UPDATE/DELETE строк.
    Триггеры ловят ЛЮБОЙ путь записи, включая прямой SQL и ad-hoc скрипты,
    которые обходят ``audit_event``. Модель нужна только для чтения при
    расследовании — см. docs/ai/task-result-audit.md.

    Границы: аудируются UPDATE и DELETE. INSERT не пишется — сама запись
    результата и есть его первое состояние, а поток вставок на порядок больше
    (на проде 22 122 вставки против 4 944 обновлений и 39 удалений).
    """

    __tablename__ = "task_result_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    result_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment=(
            "task_results.id на момент изменения. Без FK: запись должна пережить "
            "удаление результата (каскад от users/tasks)."
        ),
    )
    task_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="Снимок task_results.task_id",
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="Снимок task_results.user_id — чья оценка",
    )
    action: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="'UPDATE' | 'DELETE'",
    )
    old_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    new_score: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="NULL у DELETE: «стало» не существует, есть только снимок «было»",
    )
    old_is_correct: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    new_is_correct: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        nullable=False,
    )
    changed_by: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="app.audit_actor на момент записи; NULL = источник не назвался",
    )
    db_role: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="current_user соединения на момент записи — заполняется всегда",
    )
