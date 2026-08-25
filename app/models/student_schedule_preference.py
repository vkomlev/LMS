from __future__ import annotations

from datetime import datetime, time
from typing import Any, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StudentSchedulePreference(Base):
    """Пожелания ученика по расписанию: сколько занятий в неделю (tsk-674).

    Живёт рядом с `lesson_slot` / `lesson_slot_student`, а не вместо них:
    слот — это уже принятое решение школы, пожелание — то, что ученик просит
    ДО вёрстки. Одна действующая строка на ученика; каждое сохранение
    дополнительно кладёт снимок в `student_schedule_preference_revision`, чтобы
    история правок за весь срок обучения не терялась (требование оператора).

    Часы — в отдельной таблице `student_schedule_preference_hour`. Время в них
    ВСЕГДА московское: сетку школа ведёт по Москве, а ученику его пояс
    дорисовывает клиент (tsk-588).
    """

    __tablename__ = "student_schedule_preference"
    __table_args__ = (
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="student_schedule_preference_student_id_fkey",
        ),
        ForeignKeyConstraint(
            ["updated_by"], ["users.id"], ondelete="SET NULL",
            name="student_schedule_preference_updated_by_fkey",
        ),
        PrimaryKeyConstraint("id", name="student_schedule_preference_pkey"),
        UniqueConstraint("student_id", name="uq_student_schedule_preference_student"),
        CheckConstraint(
            "lessons_per_week BETWEEN 1 AND 7",
            name="ck_student_schedule_preference_lessons_per_week",
        ),
        {"comment": "Пожелания ученика по расписанию: занятий в неделю + часы (tsk-674)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    lessons_per_week: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=text("2"),
        comment="Сколько занятий в неделю нужно ученику; умолчание — 2",
    )
    comment: Mapped[Optional[str]] = mapped_column(
        Text, comment="Свободная приписка ученика: «после 17 не могу» и т.п."
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_by: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Кто сохранил последнюю версию: сам ученик или сотрудник"
    )


class StudentSchedulePreferenceHour(Base):
    """Один выбранный час пожелания: день недели + время начала + вид (tsk-674).

    `kind`: `preferred` — желательный час (самый предпочтительный),
    `possible` — возможный (менее интересный, но приемлемый). Один и тот же час
    не может быть одновременно желательным и возможным — это держит
    уникальность по паре «день + время».
    """

    __tablename__ = "student_schedule_preference_hour"
    __table_args__ = (
        ForeignKeyConstraint(
            ["preference_id"], ["student_schedule_preference.id"], ondelete="CASCADE",
            name="student_schedule_preference_hour_preference_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="student_schedule_preference_hour_pkey"),
        UniqueConstraint(
            "preference_id", "weekday", "start_time",
            name="uq_student_schedule_preference_hour_slot",
        ),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_schedule_preference_hour_weekday"),
        CheckConstraint(
            "kind IN ('preferred', 'possible')", name="ck_schedule_preference_hour_kind"
        ),
        {"comment": "Выбранные часы пожелания, время московское (tsk-674)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    preference_id: Mapped[int] = mapped_column(Integer, nullable=False)
    weekday: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, comment="0=понедельник .. 6=воскресенье"
    )
    start_time: Mapped[time] = mapped_column(
        Time, nullable=False, comment="Начало часа по Москве"
    )
    kind: Mapped[str] = mapped_column(
        Text, nullable=False, comment="preferred — желательный час, possible — возможный"
    )


class StudentSchedulePreferenceRevision(Base):
    """Снимок пожеланий на момент сохранения — история за весь срок обучения.

    Снимок целиком (`hours` как JSONB), а не построчный дифф: правка здесь
    редкая, а читать историю будет человек — методист, который выясняет, что
    ученик просил в августе и что попросил в ноябре. Дифф пришлось бы
    собирать обратно, снимок читается как есть.
    """

    __tablename__ = "student_schedule_preference_revision"
    __table_args__ = (
        ForeignKeyConstraint(
            ["student_id"], ["users.id"], ondelete="CASCADE",
            name="student_schedule_preference_revision_student_id_fkey",
        ),
        ForeignKeyConstraint(
            ["changed_by"], ["users.id"], ondelete="SET NULL",
            name="student_schedule_preference_revision_changed_by_fkey",
        ),
        PrimaryKeyConstraint("id", name="student_schedule_preference_revision_pkey"),
        {"comment": "История правок пожеланий ученика по расписанию (tsk-674)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    lessons_per_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    hours: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment="Снимок часов: [{weekday, start_time, kind}, …], время московское",
    )
    comment: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'student'"),
        comment="Кто и откуда правил: student | onboarding | staff",
    )
    changed_by: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
