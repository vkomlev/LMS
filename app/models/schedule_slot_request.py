from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ScheduleSlotRequest(Base):
    """Заявка ученика «не нашёл подходящее время» (tsk-674, фаза 3).

    Почему таблица, а не одно уведомление методисту. Уведомление — это сигнал,
    а сигнал в этой школе уже дважды тонул: механизм срабатывал, письмо
    уходило, и оставалось непрочитанным (tsk-591, tsk-652). Заявка со статусом
    даёт то, чего у уведомления нет, — очередь, в которой видно, что ещё не
    разобрано. Уведомление при этом никуда не девается: оно ведёт в очередь.

    Снимок пожеланий (`hours`, `lessons_per_week`) хранится прямо здесь, а не
    читается по ссылке в момент открытия: методист разбирает заявку через
    день-другой, а ученик к тому времени мог поправить пожелания. Разговор
    должен идти о том, с чем человек нажал кнопку.
    """

    __tablename__ = "schedule_slot_request"
    __table_args__ = (
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="schedule_slot_request_student_id_fkey",
        ),
        ForeignKeyConstraint(
            ["resolved_by"], ["users.id"], ondelete="SET NULL",
            name="schedule_slot_request_resolved_by_fkey",
        ),
        PrimaryKeyConstraint("id", name="schedule_slot_request_pkey"),
        CheckConstraint(
            "status IN ('open', 'resolved')", name="ck_schedule_slot_request_status"
        ),
        {"comment": "Заявки учеников «не нашёл подходящее время» (tsk-674 фаза 3)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(
        Text, comment="Что именно не подошло — словами ученика"
    )
    lessons_per_week: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=text("2"),
        comment="Снимок: сколько занятий в неделю просил ученик на момент заявки",
    )
    hours: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment="Снимок пожеланий: [{weekday, start_time, kind}, …], время московское",
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'open'"),
        comment="open — ждёт методиста, resolved — разобрано",
    )
    resolution_note: Mapped[Optional[str]] = mapped_column(
        Text, comment="Чем кончилось: добавили слот / договорились / записался сам"
    )
    resolved_by: Mapped[Optional[int]] = mapped_column(Integer)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
