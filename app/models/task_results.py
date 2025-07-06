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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.tasks import Tasks
    from app.models.users import Users
    
class TaskResults(Base):
    """
    Результаты выполнения заданий пользователями.
    """
    __tablename__ = "task_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"],
            ondelete="CASCADE", name="assignment_results_assignment_id_fkey"
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            ondelete="CASCADE", name="assignment_results_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="assignment_results_pkey"),
        Index("idx_assignment_results", "user_id", "task_id"),
        {"comment": "Результаты выполнения заданий"},
    )

    id: Mapped[int] = mapped_column(
        Integer,
        text("nextval('assignment_results_id_seq')"),
        primary_key=True,
        comment="ID результата"
    )
    score: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Оценка"
    )
    user_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="ID пользователя"
    )
    task_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="ID задания"
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        comment="Время сдачи"
    )
    metrics: Mapped[Optional[dict]] = mapped_column(
        JSONB, comment="Метрики качества ответа"
    )
    count_retry: Mapped[int] = mapped_column(
        SmallInteger, server_default=text("0"), nullable=False, comment="Попыток"
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        comment="Когда начали"
    )
    

    task: Mapped["Tasks"] = relationship("Tasks", back_populates="task_results")
    user: Mapped["Users"] = relationship("Users", back_populates="task_results")
