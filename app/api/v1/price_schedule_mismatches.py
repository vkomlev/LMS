"""tsk-1024: ученики, у которых ручная цена расходится с расписанием.

Сигнал `discrepancy` считался с tsk-557, но дальше ответа API не шёл — его не
читал ни один экран. Здесь он отдаётся списком для карточки ученика и значков
в списках учеников. Видят преподаватель, методист, маркетолог и админ; ученику
и родителю — нет. Read-only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.schemas.pricing import PriceScheduleMismatchRead
from app.services import manual_progress_service, pricing_service, roles_service

router = APIRouter(tags=["price_schedule_mismatches"])

_ROLE_GATE = require_role("teacher", "methodist", "marketer", "admin")


async def _staff_gate(current_user: CurrentUser = Depends(_ROLE_GATE)) -> CurrentUser:
    """Только живой сотрудник: сервисный ключ видел бы ФИО и цены учеников."""
    if current_user.is_service:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Доступно только сотруднику")
    return current_user


@router.get(
    "/staff/price-schedule-mismatches",
    response_model=list[PriceScheduleMismatchRead],
    summary="Ученики, у которых ручная цена не совпадает с расписанием",
)
async def list_price_schedule_mismatches(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_staff_gate),
) -> list[PriceScheduleMismatchRead]:
    """Действующие расхождения (их единицы — фильтр по ученику на клиенте).

    Маркетолог, методист и админ видят всю школу. Преподаватель — только своих
    учеников (тот же ACL, что у правки прогресса): цена чужого ученика ему не
    нужна, и раздавать её всем преподавателям незачем.
    """
    rows = await pricing_service.list_price_schedule_mismatches(db)
    roles = {r.lower().strip() for r in await roles_service.get_user_role_names(db, current_user.id)}
    if not roles & {"marketer", "methodist", "admin"}:
        rows = [
            r for r in rows
            if await manual_progress_service.can_edit_progress(db, current_user, r["student_id"])
        ]
    return [PriceScheduleMismatchRead(**r) for r in rows]
