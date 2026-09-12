"""Дашборд маркетолога: агрегаты шести KPI-плиток главной страницы (tsk-925).

Ничего не пересчитывает параллельно денежному контуру: начисления берутся
через `charge_service.list_charges()` и его готовое поле `total_minor`
(единственная формула суммы месяца — `charge_service.charge_total_minor()`,
tsk-010), долг ушедших — через `payment_reminder_service.list_alumni_debts()`
(tsk-805). Собственная логика — только там, где готового счётчика ещё не было:
лиды, переходы на платный тариф/в выпускники, конверсия, необработанные лиды.

Предикат «клиент» — единственное в этом модуле, что не лежит в постановке
буквально и проверено вручную по боевой базе перед реализацией (`/db-check`,
2026-09-12). `subscription_plan.code = 'base_legacy'` в предикат НЕ входит:
08.08.2026 туда разом легло 37 строк с причиной «tsk-301 этап 5: пакетная
миграция» (и ещё ~20 точечных правок тем же днём) — то есть весь август
код дал бы ~70 «новых клиентов» вместо ~9 реальных, если считать код тарифа
как есть. `code NOT IN ('alumni', 'base_legacy') AND pricing_group_id IS NOT
NULL` совпадает с контрольными числами черновой разведки задачи (26 клиентов и
7 выпускников в сентябре 2026, 9 и 8 в августе — расхождение на 0-1 в пользу
редких тарифов adults/ai, которые разведка не считала явно, а по смыслу это
тоже платный переход).
"""
from __future__ import annotations

from datetime import date
from typing import Callable, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.marketer_dashboard import (
    ClientsAlumniKpi,
    ConversionKpi,
    ConversionPoint,
    KpiSeries,
    MarketerDashboardKpi,
    MonthlyPoint,
)
from app.services import charge_service, payment_reminder_service

__all__ = [
    "get_dashboard",
    "DEFAULT_HISTORY_MONTHS",
    "DEFAULT_UNPROCESSED_THRESHOLD_DAYS",
]

#: Alumni — отдельная категория, не «клиент». base_legacy — доказанный на
#: боевой базе технический код массовых миграций (см. docstring модуля), не
#: сигнал воронки продаж.
_ALUMNI_PLAN_CODE = "alumni"
_LEGACY_MIGRATION_PLAN_CODE = "base_legacy"

DEFAULT_HISTORY_MONTHS = 6
DEFAULT_UNPROCESSED_THRESHOLD_DAYS = 3


def _prev_month(period: date) -> date:
    """Первое число предыдущего месяца."""
    if period.month == 1:
        return date(period.year - 1, 12, 1)
    return date(period.year, period.month - 1, 1)


def _history_start(period: date, months: int) -> date:
    start = period
    for _ in range(months - 1):
        start = _prev_month(start)
    return start


def _month_series(start: date, end_inclusive: date) -> list[date]:
    """Первые числа месяцев от `start` до `end_inclusive` включительно."""
    months: list[date] = []
    cursor = start
    while cursor <= end_inclusive:
        months.append(cursor)
        cursor = charge_service.next_month(cursor)
    return months


def _delta_pct(current: int, previous: int) -> Optional[float]:
    """Проценты не считаются от нуля — делить не на что, не «бесконечный рост»."""
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


async def get_dashboard(
    db: AsyncSession,
    *,
    period: Optional[date] = None,
    months: int = DEFAULT_HISTORY_MONTHS,
    unprocessed_threshold_days: int = DEFAULT_UNPROCESSED_THRESHOLD_DAYS,
) -> MarketerDashboardKpi:
    target = charge_service.month_start(period or date.today())
    previous = _prev_month(target)
    history_start = _history_start(target, months)
    history_months = _month_series(history_start, target)
    range_end = charge_service.next_month(target)  # исключающая правая граница

    leads_by_month = await _leads_and_conversion_by_month(
        db, start=history_start, end=range_end
    )
    clients_alumni_by_month = await _clients_alumni_by_month(
        db, start=history_start, end=range_end
    )
    charges_by_month = {m: await _charges_total_for_month(db, m) for m in history_months}

    leads_series = _build_kpi_series(
        history_months, target, previous, lambda m: leads_by_month.get(m, (0, 0))[0]
    )
    clients_series = _build_kpi_series(
        history_months, target, previous,
        lambda m: clients_alumni_by_month.get(m, (0, 0))[0],
    )
    alumni_series = _build_kpi_series(
        history_months, target, previous,
        lambda m: clients_alumni_by_month.get(m, (0, 0))[1],
    )
    charges_series = _build_kpi_series(
        history_months, target, previous, lambda m: charges_by_month.get(m, 0)
    )
    conversion = _build_conversion(history_months, target, previous, leads_by_month)

    unprocessed_count = await _unprocessed_leads_count(db, unprocessed_threshold_days)
    alumni_debts = await payment_reminder_service.list_alumni_debts(db)

    return MarketerDashboardKpi(
        period=target,
        leads=leads_series,
        clients_alumni=ClientsAlumniKpi(clients=clients_series, alumni=alumni_series),
        charges=charges_series,
        conversion=conversion,
        unprocessed_leads_count=unprocessed_count,
        unprocessed_leads_threshold_days=unprocessed_threshold_days,
        alumni_debt_total_minor=sum(d.due_minor for d in alumni_debts),
        alumni_debt_count=len(alumni_debts),
    )


def _build_kpi_series(
    history_months: list[date],
    current_period: date,
    previous_period: date,
    value_for_month: Callable[[date], int],
) -> KpiSeries:
    current = value_for_month(current_period)
    previous = value_for_month(previous_period)
    return KpiSeries(
        current=current,
        previous=previous,
        delta_abs=current - previous,
        delta_pct=_delta_pct(current, previous),
        history=[MonthlyPoint(period=m, value=value_for_month(m)) for m in history_months],
    )


def _conversion_rate(leads_count: int, converted_count: int) -> float:
    return round(converted_count / leads_count * 100, 1) if leads_count else 0.0


def _build_conversion(
    history_months: list[date],
    current_period: date,
    previous_period: date,
    leads_by_month: dict[date, tuple[int, int]],
) -> ConversionKpi:
    current_leads, current_converted = leads_by_month.get(current_period, (0, 0))
    previous_leads, previous_converted = leads_by_month.get(previous_period, (0, 0))
    current_rate = _conversion_rate(current_leads, current_converted)
    previous_rate = _conversion_rate(previous_leads, previous_converted)

    history: list[ConversionPoint] = []
    for m in history_months:
        leads_count, converted_count = leads_by_month.get(m, (0, 0))
        history.append(
            ConversionPoint(
                period=m,
                rate_pct=_conversion_rate(leads_count, converted_count),
                leads_count=leads_count,
                converted_count=converted_count,
            )
        )

    return ConversionKpi(
        current_rate_pct=current_rate,
        previous_rate_pct=previous_rate,
        delta_pct=(
            round(current_rate - previous_rate, 1)
            if previous_rate or current_rate
            else None
        ),
        current_leads_count=current_leads,
        current_converted_count=current_converted,
        # Текущий календарный месяц ещё идёт — часть его лидов станет клиентами
        # позже, и число вырастет постфактум. Это не ошибка расчёта.
        current_period_is_partial=current_period >= charge_service.month_start(date.today()),
        history=history,
    )


async def _leads_and_conversion_by_month(
    db: AsyncSession, *, start: date, end: date
) -> dict[date, tuple[int, int]]:
    """Лиды по месяцу появления + сколько из них когда-либо стали клиентами.

    Конверсия считается по когорте месяца ПОЯВЛЕНИЯ лида, а не по месяцу
    платного перехода: лид сентября мог стать клиентом в сентябре или позже,
    поэтому подзапрос перехода намеренно не ограничен диапазоном `start`/`end`
    — иначе поздняя конверсия сентябрьского лида терялась бы.
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT date_trunc('month', l.created_at)::date AS month,
                       count(*) AS leads_count,
                       count(*) FILTER (
                         WHERE l.linked_student_id IS NOT NULL
                           AND EXISTS (
                             SELECT 1
                               FROM student_subscription s
                               JOIN subscription_plan p ON p.id = s.plan_id
                              WHERE s.student_id = l.linked_student_id
                                AND s.pricing_group_id IS NOT NULL
                                AND p.code != :alumni_code
                                AND p.code != :legacy_code
                           )
                       ) AS converted_count
                  FROM leads l
                 WHERE l.created_at >= :start AND l.created_at < :end
                 GROUP BY 1
                """
            ),
            {
                "start": start,
                "end": end,
                "alumni_code": _ALUMNI_PLAN_CODE,
                "legacy_code": _LEGACY_MIGRATION_PLAN_CODE,
            },
        )
    ).all()
    return {r.month: (int(r.leads_count), int(r.converted_count)) for r in rows}


async def _clients_alumni_by_month(
    db: AsyncSession, *, start: date, end: date
) -> dict[date, tuple[int, int]]:
    """Переходы на платный тариф / в выпускники по месяцу СОБЫТИЯ (`starts_on`).

    Без фильтра по `ends_on IS NULL` — иначе потерялись бы события месяца у
    ученика, который с тех пор сменил тариф ещё раз (действующей осталась бы
    только последняя строка, а не та, что отмечает переход именно в этом
    месяце).
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT date_trunc('month', s.starts_on)::date AS month,
                       count(*) FILTER (
                         WHERE s.pricing_group_id IS NOT NULL
                           AND p.code != :alumni_code
                           AND p.code != :legacy_code
                       ) AS new_clients,
                       count(*) FILTER (WHERE p.code = :alumni_code) AS new_alumni
                  FROM student_subscription s
                  JOIN subscription_plan p ON p.id = s.plan_id
                 WHERE s.starts_on >= :start AND s.starts_on < :end
                 GROUP BY 1
                """
            ),
            {
                "start": start,
                "end": end,
                "alumni_code": _ALUMNI_PLAN_CODE,
                "legacy_code": _LEGACY_MIGRATION_PLAN_CODE,
            },
        )
    ).all()
    return {r.month: (int(r.new_clients), int(r.new_alumni)) for r in rows}


async def _charges_total_for_month(db: AsyncSession, period: date) -> int:
    """Сумма начислений месяца — через готовую формулу `charge_service`.

    Не пересчитывается заново: `list_charges()` уже применяет
    `charge_total_minor()`, единственное место формулы (tsk-010).
    """
    rows = await charge_service.list_charges(db, period=period)
    return sum(r["total_minor"] for r in rows)


async def _unprocessed_leads_count(db: AsyncSession, threshold_days: int) -> int:
    """Лиды старше `threshold_days` без примечания и без привязки к ученику."""
    row = (
        await db.execute(
            text(
                "SELECT count(*) FROM leads "
                " WHERE note IS NULL AND linked_student_id IS NULL "
                "   AND created_at < now() - (:days * interval '1 day')"
            ),
            {"days": threshold_days},
        )
    ).scalar_one()
    return int(row)
