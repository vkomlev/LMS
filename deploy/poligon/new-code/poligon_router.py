"""Polygon-only эндпоинты: каталог/промокод/checkout/enroll (tsk-182).

Ветка `poligon` ТОЛЬКО. В `app/api/main.py` этой ветки:
    from app.api.v1.poligon import router as poligon_router
    app.include_router(poligon_router, prefix="/api/v1")

Содержит 2 намеренных дефекта (классы 2 и 3 реестра, docs/qa-poligon/
defect-registry.md) — см. комментарии `# DEFECT` на месте. Всё остальное —
обычный, корректный код (не нужно засеивать баг в КАЖДОЙ строке — полигон
должен в целом работать, иначе дефекты не будут выделяться на фоне шума).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user, CurrentUser
from app.models.courses import Courses
from app.schemas.poligon import (
    CatalogCourseOut,
    CheckoutRequest,
    CheckoutResponse,
    EnrollRequest,
    EnrollResponse,
    PromoApplyRequest,
    PromoApplyResponse,
)
from sqlalchemy import text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/poligon", tags=["poligon"])


@router.get("/catalog", response_model=list[CatalogCourseOut])
async def get_catalog(db: AsyncSession = Depends(get_async_db)) -> list[CatalogCourseOut]:
    """Публичный каталог — только курсы полигона (external_uid LIKE 'poligon-%'),
    аномалия для Г9 (`poligon-sql-anomaly-*`) сознательно НЕ включена в выдачу —
    она находится только прямым SQL-запросом к БД, не через API."""
    result = await db.execute(
        select(Courses).where(
            Courses.external_uid.like("poligon-%"),
            ~Courses.external_uid.like("poligon-sql-anomaly-%"),
        )
    )
    return [
        CatalogCourseOut(
            id=c.id, external_uid=c.external_uid, title=c.title, price=float(c.price)
        )
        for c in result.scalars().all()
    ]


async def _get_promo(db: AsyncSession, code: str) -> tuple[str, int] | None:
    row = (
        await db.execute(
            text(
                "SELECT code, discount_percent FROM poligon_promo_codes WHERE code = :code"
            ),
            {"code": code},
        )
    ).first()
    return (row.code, row.discount_percent) if row else None


@router.post("/promo/apply", response_model=PromoApplyResponse)
async def apply_promo(
    body: PromoApplyRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> PromoApplyResponse:
    promo = await _get_promo(db, body.code)
    if promo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Промокод не найден")
    code, discount_percent = promo

    # DEFECT (класс 8 реестра): здесь ДОЛЖНА быть проверка "уже применялся
    # этим пользователем" (SELECT в poligon_promo_redemptions по (user_id,
    # promo_code) ДО INSERT) — сознательно опущена. INSERT происходит всегда,
    # без уникального ограничения на уровне таблицы (см. migration) —
    # повторное применение снова возвращает promo_applied=true.
    await db.execute(
        text(
            "INSERT INTO poligon_promo_redemptions (user_id, promo_code) VALUES (:u, :c)"
        ),
        {"u": current_user.id, "c": code},
    )
    await db.commit()

    return PromoApplyResponse(promo_applied=True, discount_percent=discount_percent)


@router.post("/checkout", response_model=CheckoutResponse)
async def checkout(
    body: CheckoutRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> CheckoutResponse:
    course = (
        await db.execute(select(Courses).where(Courses.id == body.course_id))
    ).scalar_one_or_none()
    if course is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Курс не найден")

    base_price = float(course.price)
    discount_percent = 0
    promo_applied = False
    if body.promo_code:
        promo = await _get_promo(db, body.promo_code)
        if promo is not None:
            _, discount_percent = promo
            promo_applied = True

    # DEFECT (класс 2 реестра, урок 6.4): скидка не применяется в ветке
    # payment_method == "card" — сумма списания равна полной цене, хотя
    # ответ ниже (promo_applied/discount_percent) честно отражает, что
    # промокод был распознан. Ветка "wallet" считает корректно.
    if promo_applied and body.payment_method == "wallet":
        amount_charged = round(base_price * (1 - discount_percent / 100), 2)
    else:
        amount_charged = base_price  # DEFECT: для payment_method="card" это неверно,
        # если promo_applied=True — должно быть amount_charged со скидкой.

    order_id_row = (
        await db.execute(
            text(
                "INSERT INTO poligon_enrollments (user_id, course_id, payment_method, amount_charged) "
                "VALUES (:u, :c, :pm, :amt) "
                "ON CONFLICT (user_id, course_id) DO UPDATE SET amount_charged = EXCLUDED.amount_charged "
                "RETURNING id"
            ),
            {
                "u": current_user.id,
                "c": course.id,
                "pm": body.payment_method,
                "amt": amount_charged,
            },
        )
    ).scalar_one()
    await db.commit()

    return CheckoutResponse(
        order_id=order_id_row,
        promo_applied=promo_applied,
        discount_percent=discount_percent,
        amount_charged=amount_charged,
    )


@router.post("/enroll", response_model=EnrollResponse)
async def enroll(
    body: EnrollRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> EnrollResponse:
    """DEFECT (класс 3 реестра): исключение из БД (например, гонка/конфликт)
    намеренно проглатывается без re-raise и без отдельного commit — ответ
    всегда 200/enrolled=true, даже если запись физически не создалась.
    Это НЕ демонстрация «как правильно» — это воспроизведение реального
    класса ошибок (см. project_lms_service_side_effects в памяти проекта:
    коммит-в-чужой-транзакции — одна из самых частых причин именно такого
    расхождения в проде)."""
    try:
        await db.execute(
            text(
                "INSERT INTO user_courses (user_id, course_id, is_active) "
                "VALUES (:u, :c, TRUE)"
            ),
            {"u": current_user.id, "c": body.course_id},
        )
        # DEFECT: commit намеренно НЕ вызван здесь — транзакция откатится при
        # закрытии сессии (или на следующем db.rollback() где-то выше по стеку
        # запроса), а обработчик уже вернул enrolled=true.
    except Exception:
        logger.warning("poligon enroll: insert failed silently (by design, defect class 3)")

    return EnrollResponse(enrolled=True)
