from __future__ import annotations

from typing import TYPE_CHECKING, Optional
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Integer,
    SmallInteger,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    Index,
    text,
    Boolean,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.tasks import Tasks
    from app.models.users import Users
    from app.models.attempts import Attempts  # новый модуль, см. ниже


class TaskResults(Base):
    """
    Результаты выполнения заданий пользователями.
    """
    __tablename__ = "task_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="CASCADE",
            name="assignment_results_assignment_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="assignment_results_user_id_fkey",
        ),
        ForeignKeyConstraint(
            ["attempt_id"],
            ["attempts.id"],
            ondelete="CASCADE",
            name="task_results_attempt_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="assignment_results_pkey"),
        Index("idx_assignment_results", "user_id", "task_id"),
        {"comment": "Результаты выполнения заданий"},
    )

    id: Mapped[int] = mapped_column(
        Integer,
        server_default=text("nextval('assignment_results_id_seq')"),
        primary_key=True,
        comment="ID результата",
    )
    score: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Оценка",
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="ID пользователя",
    )
    task_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="ID задания",
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        comment="Время сдачи",
    )
    metrics: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        comment="Метрики качества ответа",
    )
    count_retry: Mapped[int] = mapped_column(
        SmallInteger,
        server_default=text("0"),
        nullable=False,
        comment="Попыток",
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        comment="Когда начали",
    )

    # ---------- Новые поля для stateful-проверки ----------

    attempt_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="ID попытки (attempts.id)",
    )
    answer_json: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Исходный ответ ученика (StudentAnswer/StudentResponse)",
    )
    max_score: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Максимальный балл за задачу на момент проверки",
    )
    is_correct: Mapped[Optional[bool]] = mapped_column(
        Boolean,
        nullable=True,
        comment="Флаг правильности ответа (None для задач с ручной проверкой)",
    )
    checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Время проверки",
    )
    checked_by: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="ID пользователя, выполнившего проверку (NULL — авто)",
    )
    source_system: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'system'"),
        comment="Источник системы, записавшей результат",
    )

    # ---------- Этап 3.9: claim для ручной проверки ----------
    review_claimed_by: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="ID преподавателя, захватившего проверку",
    )
    review_claim_token: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        comment="Токен блокировки проверки",
    )
    review_claim_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Истечение блокировки проверки",
    )

    # ---------- Связи ----------

    task: Mapped["Tasks"] = relationship(
        "Tasks",
        back_populates="task_results",
    )
    user: Mapped["Users"] = relationship(
        "Users",
        back_populates="task_results",
    )
    attempt: Mapped[Optional["Attempts"]] = relationship(
        "Attempts",
        back_populates="task_results",
    )
    
    #checked_by_user: Mapped[Optional["Users"]] = relationship(
    #    "Users",
    #    foreign_keys=[checked_by],
    #)
