"""Дашборд маркетолога: KPI-плитки главной страницы кабинета (tsk-925)."""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel


class MonthlyPoint(BaseModel):
    """Одна точка истории графика — месяц и значение."""

    period: date
    value: int


class KpiSeries(BaseModel):
    """Текущий период, прошлый — для справки, прирост, история по месяцам."""

    current: int
    previous: int
    delta_abs: int
    #: Проценты не считаются от нуля — делить не на что, а не «бесконечный рост».
    delta_pct: Optional[float] = None
    history: list[MonthlyPoint]


class ConversionPoint(BaseModel):
    """Конверсия одного месяца-когорты: сколько лидов, сколько из них стали клиентами."""

    period: date
    rate_pct: float
    leads_count: int
    converted_count: int


class ConversionKpi(BaseModel):
    """Конверсия лидов в клиенты по когорте месяца появления лида.

    `current_period_is_partial` —tsk-925: конверсия текущего (незакрытого)
    месяца всегда занижена относительно будущей, потому что часть лидов этого
    месяца ещё станет клиентами позже. Это свойство метрики с задержкой, а не
    дефект расчёта — фронтенд обязан показать оговорку рядом с числом.
    """

    current_rate_pct: float
    previous_rate_pct: float
    delta_pct: Optional[float] = None
    current_leads_count: int
    current_converted_count: int
    current_period_is_partial: bool
    history: list[ConversionPoint]


class ClientsAlumniKpi(BaseModel):
    """Одна плитка, два числа: переход на платный тариф и выпуск (tsk-925)."""

    clients: KpiSeries
    alumni: KpiSeries


class MarketerDashboardKpi(BaseModel):
    """Шесть плиток главной страницы кабинета маркетолога."""

    period: date
    leads: KpiSeries
    clients_alumni: ClientsAlumniKpi
    #: Деньги в копейках, как везде в денежном контуре.
    charges: KpiSeries
    conversion: ConversionKpi
    unprocessed_leads_count: int
    unprocessed_leads_threshold_days: int
    alumni_debt_total_minor: int
    alumni_debt_count: int
