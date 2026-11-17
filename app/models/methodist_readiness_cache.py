from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import JSON, Date, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MethodistReadinessCache(Base):
    """Материализованный вердикт «успевает к экзамену» (tsk-1536).

    Одна строка на ученика программы подготовки ЕГЭ/ОГЭ. Пишет фоновый тик
    ``methodist_readiness_cron_service`` (расчёт tsk-815 — тяжёлый, ~8–12 с
    на ученика на прод-данных, поэтому в HTTP-запросе его нет: два инцидента
    504 17.11); дашборд методиста читает таблицу одним запросом.

    ``on_track`` NULL — темп измерить не по чему («нет данных»), а не
    «не успевает»: это разные состояния для методиста.
    """

    __tablename__ = "methodist_readiness_cache"
    __table_args__ = (
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="methodist_readiness_cache_student_id_fkey",
        ),
        PrimaryKeyConstraint("student_id", name="methodist_readiness_cache_pkey"),
        {"comment": "Вердикт «успевает к экзамену» для дашборда методиста (tsk-1536)"},
    )

    student_id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(length=8), nullable=False)
    deadline: Mapped[date] = mapped_column(Date, nullable=False)
    on_track: Mapped[Optional[bool]] = mapped_column(nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, server_default=text("'{}'::json")
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
