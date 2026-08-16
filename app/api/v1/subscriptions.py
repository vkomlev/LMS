"""Управление тарифами персоналом (tsk-301, Фаза 9).

Гейт `marketer|admin`. **Преподавателю нельзя** (решение 10 брифа): тариф — это
деньги и права, а не учебная работа; преподаватель распоряжается занятиями.
Методиста здесь тоже нет — по той же границе, что и в кабинете маркетолога.

Права включаются сразу, деньги — со следующего месяца (решение 14). Второе
обеспечивает не этот код, а резолвер группы: он берёт строку подписки,
действовавшую **на первое число расчётного месяца** (tsk-585). Поэтому смена
тарифа посреди месяца не переписывает уже названную человеку сумму, и делать
здесь для этого ничего не нужно — важно ровно НЕ звать пересчёт.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.schemas.subscription import (
    StudentSubscriptionState,
    SubscriptionChangeRequest,
    SubscriptionPlanRead,
)
from app.services import audit_service, subscription_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

_ROLE_GATE = require_role("marketer", "admin")


async def _staff_gate(
    current_user: CurrentUser = Depends(_ROLE_GATE),
) -> CurrentUser:
    """Роль marketer/admin И живой человек, а не сервисный ключ.

    `require_role` пропускает сервисный токен без проверки роли — это сделано
    ради ботов. Здесь так нельзя: держатель ключа TG_LMS менял бы тарифы, а в
    истории оставалось бы `changed_by = NULL` — то есть «неизвестно кто». Смена
    тарифа меняет и права, и сумму месяца; безымянная смена неразбираема.
    """
    if current_user.is_service:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Управление тарифами доступно только пользователю, не сервисному ключу",
        )
    return current_user


async def _require_user(db: AsyncSession, student_id: int) -> None:
    """404 на несуществующего ученика.

    Без проверки присвоение упало бы 500 на внешнем ключе, а чтение молча
    вернуло бы пустую историю — «тарифа нет» вместо «такого человека нет».
    """
    exists = await db.scalar(
        text("SELECT 1 FROM users WHERE id = :id"), {"id": student_id}
    )
    if not exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")


@router.get(
    "/plans",
    response_model=list[SubscriptionPlanRead],
    summary="Все тарифы с правами — витрина персонала",
    description=(
        "Весь набор, включая непродаваемые (`test`, `base_legacy`, «Выпускник»). "
        "Витрина покупки — другой адрес: `GET /payments/plans`, там только то, "
        "что человек может купить сам."
    ),
)
async def list_plans(
    db: AsyncSession = Depends(get_async_db),
    _staff: CurrentUser = Depends(_staff_gate),
) -> list[SubscriptionPlanRead]:
    """Тарифы с правами и тарифной группой."""
    return [SubscriptionPlanRead(**row) for row in await subscription_service.list_plans(db)]


@router.get(
    "/students/{student_id}",
    response_model=StudentSubscriptionState,
    summary="Тариф ученика: действующий и история",
)
async def read_student_subscription(
    student_id: int = Path(..., description="ID ученика"),
    db: AsyncSession = Depends(get_async_db),
    _staff: CurrentUser = Depends(_staff_gate),
) -> StudentSubscriptionState:
    """Что действует сейчас и как к этому пришли."""
    await _require_user(db, student_id)
    return StudentSubscriptionState(**await subscription_service.student_state(db, student_id))


@router.post(
    "/students/{student_id}",
    response_model=StudentSubscriptionState,
    summary="Присвоить ученику тариф",
    description=(
        "Права меняются сразу, сумма текущего месяца — нет (решение 14): деньги "
        "считаются по тарифу, действовавшему на первое число месяца."
    ),
    responses={
        404: {"description": "Нет такого ученика или тарифа"},
        409: {"description": "Ученик уже на этом тарифе либо тариф сменили параллельно"},
    },
)
async def change_student_subscription(
    request: Request,
    student_id: int = Path(..., description="ID ученика"),
    body: SubscriptionChangeRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_staff_gate),
) -> StudentSubscriptionState:
    """Сменить тариф ученику и записать, кто и зачем это сделал.

    Исходы разведены намеренно: сервис отвечает одним `False` и на «нет такого
    тарифа», и на «уже на нём», и на гонку. Для человека это три разных ответа —
    опечатка в коде тарифа, пустое действие и «кто-то успел раньше».
    """
    await _require_user(db, student_id)

    before = await subscription_service.student_state(db, student_id)
    current_code = (before["current"] or {}).get("plan_code")

    plan_exists = await db.scalar(
        text("SELECT 1 FROM subscription_plan WHERE code = :c AND is_active"),
        {"c": body.plan_code},
    )
    if not plan_exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Тариф не найден")
    if current_code == body.plan_code:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Ученик уже на тарифе «{body.plan_code}»"
        )

    changed = await subscription_service.change_plan(
        db,
        student_id,
        body.plan_code,
        reason=body.reason,
        changed_by=current_user.id,
    )
    if not changed:
        # Проверки выше прошли, значит между ними и сменой успел кто-то ещё.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Тариф сменили параллельно — обновите страницу"
        )

    await audit_service.log_event(
        db,
        audit_service.STAFF_SUBSCRIPTION_CHANGED,
        user_id=current_user.id,
        ip=request.client.host if request.client else None,
        details={
            "student_id": student_id,
            "from_plan": current_code,
            "to_plan": body.plan_code,
            "reason": body.reason,
        },
    )
    await db.commit()
    logger.info(
        "tsk-301: %s сменил тариф ученику %s: %s → %s",
        current_user.id, student_id, current_code, body.plan_code,
    )

    return StudentSubscriptionState(**await subscription_service.student_state(db, student_id))
