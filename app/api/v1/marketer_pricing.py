"""Тарифы курсов — кабинет маркетолога (tsk-505).

Гейт `marketer|admin`. Методист сюда не входит намеренно: он отвечает за учебное
содержание, а не за деньги — это разные зоны ответственности.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.schemas.pricing import (
    CoursePricingRead,
    CoursePricingUpdateRequest,
    PricingGroupCreateRequest,
    PricingGroupRead,
    PricingGroupUpdateRequest,
    StudentPricingRead,
    TariffCreateRequest,
    TariffUpdateRequest,
)
from app.services import charge_service, pricing_service

router = APIRouter(prefix="/marketer", tags=["marketer_pricing"])

_PRICING_ROLE_GATE = require_role("marketer", "admin")


async def _pricing_gate(
    current_user: CurrentUser = Depends(_PRICING_ROLE_GATE),
) -> CurrentUser:
    """Гейт кабинета: роль marketer/admin И живой человек, не сервисный ключ.

    `require_role` пропускает сервисный токен БЕЗ проверки роли — это осознанно
    сделано ради ботов. Здесь это не годится дважды: держатель legacy-ключа
    TG_LMS читал бы ФИО всех платящих учеников с их ценами, а правка цены
    записывалась бы без следа «кто менял» (`updated_by = NULL`).
    """
    if current_user.is_service:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Кабинет маркетолога доступен только пользователю, не сервисному ключу",
        )
    return current_user


_PRICING_GATE = _pricing_gate


@router.get(
    "/pricing/courses",
    response_model=list[CoursePricingRead],
    summary="Корневые курсы и их цены",
    description=(
        "Только корневые курсы: зачисление в LMS идёт на корень, поэтому цену "
        "вложенному курсу назначить некому. Курс без строки цены — «не назначено», "
        "и это не то же самое, что «не продаётся»."
    ),
)
async def list_course_pricing(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_PRICING_GATE),
) -> list[CoursePricingRead]:
    return await pricing_service.list_course_pricing(db)


@router.put(
    "/pricing/courses/{course_id}",
    response_model=CoursePricingRead,
    summary="Назначить курсу статус продаваемости и тарифную группу",
)
async def set_course_pricing(
    course_id: int,
    body: CoursePricingUpdateRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_PRICING_GATE),
) -> CoursePricingRead:
    # Проверка ДО записи: цена вложенному курсу не назначается, а внешний ключ
    # этого не ловит — иначе строка создавалась бы, коммитилась и всё равно
    # оборачивалась 404, оставляя мусор в базе.
    if not await pricing_service.is_root_course(db, course_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Курс не найден среди курсов верхнего уровня — цена назначается только им",
        )

    try:
        await pricing_service.set_course_pricing(
            db,
            course_id=course_id,
            sale_status=body.sale_status,
            group_id=body.group_id,
            note=body.note,
            updated_by=current_user.id,
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Тарифная группа не найдена"
        ) from exc

    rows = await pricing_service.list_course_pricing(db)
    updated = next((r for r in rows if r.course_id == course_id), None)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Курс не найден")
    return updated


@router.get(
    "/pricing/groups",
    response_model=list[PricingGroupRead],
    summary="Тарифные группы с вариантами",
)
async def list_groups(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_PRICING_GATE),
) -> list[PricingGroupRead]:
    return await pricing_service.list_groups(db)


@router.post(
    "/pricing/groups",
    response_model=PricingGroupRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать тарифную группу",
)
async def create_group(
    body: PricingGroupCreateRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_PRICING_GATE),
) -> PricingGroupRead:
    try:
        group_id = await pricing_service.create_group(
            db, name=body.name, description=body.description
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Тарифная группа с таким именем уже есть"
        ) from exc
    return await _get_group_or_404(db, group_id)


@router.patch(
    "/pricing/groups/{group_id}",
    response_model=PricingGroupRead,
    summary="Изменить тарифную группу",
)
async def update_group(
    group_id: int,
    body: PricingGroupUpdateRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_PRICING_GATE),
) -> PricingGroupRead:
    ok = await pricing_service.update_group(
        db, group_id=group_id, patch=body.model_dump(exclude_unset=True)
    )
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Тарифная группа не найдена")
    return await _get_group_or_404(db, group_id)


@router.delete(
    "/pricing/groups/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    # Явный None: из-за `from __future__ import annotations` возврат `-> None`
    # доезжает до FastAPI классом NoneType и иначе считается телом ответа.
    response_model=None,
    summary="Удалить тарифную группу",
    description="Группа, назначенная курсу, не удаляется — сперва снимите её с курсов.",
)
async def delete_group(
    group_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_PRICING_GATE),
) -> None:
    try:
        ok = await pricing_service.delete_group(db, group_id=group_id)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Группа назначена курсам — сперва снимите её с них"
        ) from exc
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Тарифная группа не найдена")


@router.post(
    "/pricing/tariffs",
    response_model=PricingGroupRead,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить вариант тарифа в группу",
    description=(
        "Возвращает группу целиком — экран цен показывает варианты вместе, "
        "а не по одному."
    ),
)
async def create_tariff(
    body: TariffCreateRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_PRICING_GATE),
) -> PricingGroupRead:
    try:
        await pricing_service.create_tariff(db, payload=body.model_dump())
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Такой вариант в группе уже есть либо группа не найдена",
        ) from exc
    return await _get_group_or_404(db, body.group_id)


@router.patch(
    "/pricing/tariffs/{tariff_id}",
    response_model=list[PricingGroupRead],
    summary="Изменить вариант тарифа",
)
async def update_tariff(
    tariff_id: int,
    body: TariffUpdateRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_PRICING_GATE),
) -> list[PricingGroupRead]:
    group_id = await pricing_service.tariff_group_id(db, tariff_id)
    if group_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Вариант тарифа не найден")

    try:
        ok = await pricing_service.update_tariff(
            db, tariff_id=tariff_id, patch=body.model_dump(exclude_unset=True)
        )
    except IntegrityError as exc:
        await db.rollback()
        # Частичный уникальный индекс: две действующие точки одной оси в группе.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "В группе уже есть действующий вариант на эту же точку оси",
        ) from exc
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Вариант тарифа не найден")

    # Правка тарифа меняет расчёт для всех, кто попадает на эту группу. Без
    # пересчёта суммы остались бы старыми до следующего ручного нажатия, и экран
    # начислений показывал бы неправду. Закрытые месяцы не трогаются.
    await charge_service.recalculate_open_months_for_group(db, group_id=group_id)
    return await pricing_service.list_groups(db)


@router.delete(
    "/pricing/tariffs/{tariff_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Удалить вариант тарифа",
)
async def delete_tariff(
    tariff_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_PRICING_GATE),
) -> None:
    ok = await pricing_service.delete_tariff(db, tariff_id=tariff_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Вариант тарифа не найден")


@router.get(
    "/students/pricing",
    response_model=list[StudentPricingRead],
    summary="Ученики и их расчётная цена",
    description=(
        "Только просмотр. Частота берётся из расписания (число активных недельных "
        "слотов ученика), цена считается один раз на тарифную группу. Каждая строка "
        "показывает, как именно получилась цена — точное попадание, ближайший "
        "меньший тариф, нужен выбор человека или нет расписания."
    ),
)
async def list_student_pricing(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_PRICING_GATE),
) -> list[StudentPricingRead]:
    return await pricing_service.list_student_pricing(db)


async def _get_group_or_404(db: AsyncSession, group_id: int) -> PricingGroupRead:
    groups = await pricing_service.list_groups(db)
    group = next((g for g in groups if g.id == group_id), None)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Тарифная группа не найдена")
    return group
