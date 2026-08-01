"""tsk-511/512/513 — схемы перерывов, начислений и ручной цены."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

ChargeStatus = Literal["open", "closed"]


class BreakRead(BaseModel):
    id: int
    student_id: int
    full_name: Optional[str] = None
    starts_on: date
    ends_on: date
    note: Optional[str] = None
    created_at: Optional[datetime] = None
    #: Сколько занятий этот перерыв сейчас гасит — чтобы последствие было видно.
    paused_lessons: int = 0


class BreakWriteRequest(BaseModel):
    """Границы включительные: «с 10 по 24» закрывает и 10-е, и 24-е."""

    starts_on: date
    ends_on: date
    note: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _range_is_sane(self) -> "BreakWriteRequest":
        if self.ends_on < self.starts_on:
            raise ValueError("Конец перерыва не может быть раньше начала")
        return self


class BreakCreateRequest(BreakWriteRequest):
    student_id: int


class ChargeRead(BaseModel):
    """Начисление за месяц с расшифровкой: из чего сложилась сумма."""

    id: int
    student_id: int
    full_name: Optional[str] = None
    group_id: int
    group_name: str
    period: date
    #: Что сказал расчёт по тарифу и расписанию.
    calculated_minor: int
    #: Сумма, поставленная руками на этот месяц. Пусто — считается расчётом.
    manual_minor: Optional[int] = None
    #: Переносы с прошлых месяцев и ручные поправки, со знаком.
    adjustments_minor: int = 0
    adjustment_details: Optional[str] = None
    #: Итог: (ручная либо расчётная) + поправки.
    total_minor: int
    expected_lessons: int
    break_lessons: int
    status: ChargeStatus
    closed_at: Optional[datetime] = None
    #: У ученика стоит ручная ЦЕНА (не сумма месяца) — расчёт её не перебивает.
    has_price_override: bool = False
    override_minor: Optional[int] = None


class ManualAmountRequest(BaseModel):
    amount_minor: int = Field(ge=0)


class PriceOverrideRead(BaseModel):
    id: int
    student_id: int
    full_name: Optional[str] = None
    group_id: int
    group_name: str
    price_minor: int
    note: Optional[str] = None


class PriceOverrideRequest(BaseModel):
    student_id: int
    group_id: int
    price_minor: int = Field(ge=0)
    note: Optional[str] = Field(default=None, max_length=500)


class ClosePeriodRequest(BaseModel):
    period: date

    @model_validator(mode="after")
    def _is_month_start(self) -> "ClosePeriodRequest":
        if self.period.day != 1:
            raise ValueError("Период — первое число месяца")
        return self


class RecalculateResult(BaseModel):
    period: date
    touched: int
