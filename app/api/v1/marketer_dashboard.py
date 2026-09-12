"""Дашборд маркетолога: главная страница кабинета, шесть KPI-плиток (tsk-925)."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, require_role
from app.db.session import get_async_db
from app.schemas.marketer_dashboard import MarketerDashboardKpi
from app.services import marketer_dashboard_service

router = APIRouter(prefix="/marketer", tags=["marketer_dashboard"])

_DASHBOARD_ROLE_GATE = require_role("marketer", "admin")


async def _dashboard_gate(
    current_user: CurrentUser = Depends(_DASHBOARD_ROLE_GATE),
) -> CurrentUser:
    """См. `marketer_pricing._pricing_gate`: сервисный ключ в кабинет не пускаем."""
    if current_user.is_service:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Кабинет маркетолога доступен только пользователю, не сервисному ключу",
        )
    return current_user


@router.get(
    "/dashboard",
    response_model=MarketerDashboardKpi,
    summary="Дашборд: лиды, клиенты/выпускники, начисления, конверсия",
    description=(
        "Шесть плиток главной страницы кабинета: лиды, переход на платный тариф "
        "и в выпускники, начисления, конверсия лидов в клиенты, необработанные "
        "лиды, долг ушедших. Конверсия текущего месяца всегда занижена — часть "
        "лидов этого месяца станет клиентами позже (см. current_period_is_partial)."
    ),
)
async def dashboard(
    period: Optional[date] = Query(
        default=None, description="Первое число месяца, по умолчанию — текущий"
    ),
    months: int = Query(
        default=marketer_dashboard_service.DEFAULT_HISTORY_MONTHS,
        ge=1,
        le=24,
        description="Сколько месяцев истории отдать для графика",
    ),
    unprocessed_threshold_days: int = Query(
        default=marketer_dashboard_service.DEFAULT_UNPROCESSED_THRESHOLD_DAYS,
        ge=1,
        le=30,
        description="Лид старше скольких дней без примечания и привязки считается необработанным",
    ),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_dashboard_gate),
) -> MarketerDashboardKpi:
    return await marketer_dashboard_service.get_dashboard(
        db,
        period=period,
        months=months,
        unprocessed_threshold_days=unprocessed_threshold_days,
    )
