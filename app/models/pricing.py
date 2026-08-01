"""Тарифы курсов (tsk-505).

Ключевое решение оператора 2026-08-01: **цена привязана к ученику, а не к паре
ученик×курс**. Курс задаёт лишь тарифную ГРУППУ. Основание — живые данные прода:
календарь не знает о курсах (ни `lesson_slot`, ни `lesson_occurrence` не имеют
`course_id`), а 24 ученика из 34 зачислены сразу на пару «Python для ЕГЭ» +
«ЕГЭ по информатике» — это один продукт за 5500, а не два по 5500. Поэтому цена
считается один раз на группу, а не складывается по курсам внутри неё.

Гибкость (требование задачи «не хардкодить под частоту и сегмент») держится на
паре `match_kind`/`match_value`: новая ось тарификации — это новое значение
`match_kind`, а не миграция схемы.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: Статусы продаваемости курса. `not_for_sale` — осознанное «не продаётся»,
#: его нельзя путать с `free`: бесплатный курс продаётся за 0, а не «никак».
SALE_STATUSES = ("paid", "free", "not_for_sale")

#: Оси тарификации. `attendance_frequency` резолвится автоматически по календарю,
#: `segment` выбирается человеком (авто-выбора нет — см. pricing_service).
MATCH_KINDS = ("attendance_frequency", "segment")


class PricingGroup(Base):
    """Тарифная группа — то, за что ученик платит один раз."""

    __tablename__ = "pricing_group"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pricing_group_pkey"),
        UniqueConstraint("name", name="uq_pricing_group_name"),
        {"comment": "Тарифные группы курсов (tsk-505)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Имя группы для маркетолога — «Базовый», «ИИ-предприниматель»"
    )
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class PricingTariff(Base):
    """Вариант тарифа внутри группы."""

    __tablename__ = "pricing_tariff"
    __table_args__ = (
        ForeignKeyConstraint(
            ["group_id"], ["pricing_group.id"], ondelete="CASCADE",
            name="pricing_tariff_group_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="pricing_tariff_pkey"),
        CheckConstraint("price_minor >= 0", name="ck_pricing_tariff_price_non_negative"),
        CheckConstraint(
            "match_kind IS NULL OR match_kind IN ('attendance_frequency', 'segment')",
            name="ck_pricing_tariff_match_kind",
        ),
        {"comment": "Варианты тарифа внутри тарифной группы (tsk-505)"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(
        Text, nullable=False, comment="«2 раза в неделю», «для своих»"
    )
    price_minor: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Цена в копейках — деньги целым числом, не float"
    )
    currency: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'RUB'")
    )
    period: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'month'"), comment="За какой срок цена"
    )
    match_kind: Mapped[Optional[str]] = mapped_column(
        Text, comment="Ось тарификации: attendance_frequency | segment | NULL (единственный вариант)"
    )
    match_value: Mapped[Optional[str]] = mapped_column(
        Text, comment="Значение оси: '1'/'2' для частоты, 'insider'/'street' для сегмента"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
        comment="Берётся, когда ни один вариант не подошёл по оси",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class CoursePricing(Base):
    """Продаваемость курса и его тарифная группа — одна строка на курс.

    Отсутствие строки = «цена не назначена» (курс ещё не разбирали).
    Явный `not_for_sale` = «разобрали и решили не продавать».
    """

    __tablename__ = "course_pricing"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id"], ["courses.id"], ondelete="CASCADE",
            name="course_pricing_course_id_fkey",
        ),
        ForeignKeyConstraint(
            ["group_id"], ["pricing_group.id"], ondelete="RESTRICT",
            name="course_pricing_group_id_fkey",
        ),
        ForeignKeyConstraint(
            ["updated_by"], ["users.id"], ondelete="SET NULL",
            name="course_pricing_updated_by_fkey",
        ),
        PrimaryKeyConstraint("course_id", name="course_pricing_pkey"),
        CheckConstraint(
            "sale_status IN ('paid', 'free', 'not_for_sale')",
            name="ck_course_pricing_sale_status",
        ),
        # Платный курс без тарифной группы посчитать нельзя — запрещаем на уровне БД,
        # иначе расчёт цены ученика молча пропустит такой курс.
        CheckConstraint(
            "(sale_status = 'paid') = (group_id IS NOT NULL)",
            name="ck_course_pricing_paid_requires_group",
        ),
        {"comment": "Продаваемость курса и его тарифная группа (tsk-505)"},
    )

    course_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sale_status: Mapped[str] = mapped_column(
        Text, nullable=False, comment="paid | free | not_for_sale"
    )
    group_id: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Тарифная группа — обязательна для paid, запрещена для остальных"
    )
    note: Mapped[Optional[str]] = mapped_column(Text)
    updated_by: Mapped[Optional[int]] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
