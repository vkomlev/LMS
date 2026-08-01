"""tsk-511/512/513 — ORM-модели перерывов, начислений и ручной цены.

Сервисы ходят сырым SQL (расчёт месяца — оконный запрос по календарю), модели
нужны для единого реестра метаданных и читаемости схемы.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StudentBreak(Base):
    """Перерыв ученика. Границы включительные."""

    __tablename__ = "student_break"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    starts_on: Mapped[Date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[Date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (CheckConstraint("ends_on >= starts_on", name="ck_student_break_range"),)


class StudentPriceOverride(Base):
    """Ручная цена ученика по тарифной группе. Снимается удалением строки."""

    __tablename__ = "student_price_override"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    group_id: Mapped[int] = mapped_column(
        ForeignKey("pricing_group.id", ondelete="CASCADE")
    )
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("student_id", "group_id", name="uq_student_price_override"),
    )


class StudentMonthlyCharge(Base):
    """Начисление за месяц. Закрытое больше не пересчитывается."""

    __tablename__ = "student_monthly_charge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    group_id: Mapped[int] = mapped_column(
        ForeignKey("pricing_group.id", ondelete="RESTRICT")
    )
    period: Mapped[Date] = mapped_column(Date, nullable=False)
    calculated_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    manual_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_lessons: Mapped[int] = mapped_column(Integer, server_default="0")
    break_lessons: Mapped[int] = mapped_column(Integer, server_default="0")
    status: Mapped[str] = mapped_column(Text, server_default="open")
    closed_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("student_id", "group_id", "period", name="uq_monthly_charge"),
    )


class ChargeAdjustment(Base):
    """Поправка к месяцу: перенос с закрытого месяца либо ручная."""

    __tablename__ = "charge_adjustment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    group_id: Mapped[int] = mapped_column(
        ForeignKey("pricing_group.id", ondelete="RESTRICT")
    )
    period: Mapped[Date] = mapped_column(Date, nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    origin_period: Mapped[Date | None] = mapped_column(Date, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
