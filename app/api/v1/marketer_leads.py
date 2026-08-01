"""Лиды — мини-CRM кабинета маркетолога (tsk-506).

Тот же гейт `marketer|admin`, что у тарифов: один кабинет — один круг доступа.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.schemas.lead import (
    LeadCreateRequest,
    LeadLinkRequest,
    LeadRead,
    LeadSourceRead,
    LeadUpdateRequest,
    StudentBrief,
)
from app.services import lead_service

router = APIRouter(prefix="/marketer", tags=["marketer_leads"])

_LEADS_ROLE_GATE = require_role("marketer", "admin")


async def _leads_gate(
    current_user: CurrentUser = Depends(_LEADS_ROLE_GATE),
) -> CurrentUser:
    """См. `marketer_pricing._pricing_gate`: сервисный ключ в кабинет не пускаем."""
    if current_user.is_service:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Кабинет маркетолога доступен только пользователю, не сервисному ключу",
        )
    return current_user


_LEADS_GATE = _leads_gate


@router.get(
    "/lead-sources",
    response_model=list[LeadSourceRead],
    summary="Справочник каналов привлечения",
)
async def list_sources(
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_LEADS_GATE),
) -> list[LeadSourceRead]:
    return await lead_service.list_sources(db)


@router.get(
    "/students/search",
    response_model=list[StudentBrief],
    summary="Поиск ученика для привязки лида",
    description=(
        "Узкая выдача: только номер и имя. Общий поиск людей с персональными "
        "данными остаётся под гейтом методиста и администратора."
    ),
)
async def search_students(
    q: str = Query(min_length=2, max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_LEADS_GATE),
) -> list[StudentBrief]:
    return await lead_service.search_students(db, q=q, limit=limit)


@router.get(
    "/leads",
    response_model=list[LeadRead],
    summary="Список лидов",
    description="Фильтр `linked`: `true` — уже привязанные, `false` — ещё нет.",
)
async def list_leads(
    linked: Optional[bool] = Query(default=None),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_LEADS_GATE),
) -> list[LeadRead]:
    return await lead_service.list_leads(db, linked=linked)


@router.get("/leads/{lead_id}", response_model=LeadRead, summary="Карточка лида")
async def get_lead(
    lead_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_LEADS_GATE),
) -> LeadRead:
    lead = await lead_service.get_lead(db, lead_id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Лид не найден")
    return lead


@router.post(
    "/leads",
    response_model=LeadRead,
    status_code=status.HTTP_201_CREATED,
    summary="Завести лида",
)
async def create_lead(
    body: LeadCreateRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_LEADS_GATE),
) -> LeadRead:
    await _validate_source(db, body.source_id, body.source_detail)
    lead_id = await lead_service.create_lead(
        db,
        source_id=body.source_id,
        source_detail=body.source_detail,
        full_name=body.full_name,
        contact=body.contact,
        note=body.note,
        created_by=current_user.id,
    )
    return await _reload(db, lead_id)


@router.patch("/leads/{lead_id}", response_model=LeadRead, summary="Изменить лида")
async def update_lead(
    lead_id: int,
    body: LeadUpdateRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_LEADS_GATE),
) -> LeadRead:
    existing = await lead_service.get_lead(db, lead_id)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Лид не найден")

    patch = body.model_dump(exclude_unset=True)
    # Проверяем ИТОГОВУЮ пару «канал + приписка», а не только присланный канал.
    # Раньше правка одной приписки проверку не запускала — и лид на канале
    # «Другое» спокойно оставался с пустым источником.
    if "source_id" in patch or "source_detail" in patch:
        source_id = patch.get("source_id", existing.source_id)
        detail = patch.get("source_detail", existing.source_detail)
        await _validate_source(db, source_id, detail)

    await lead_service.update_lead(db, lead_id=lead_id, patch=patch)
    return await _reload(db, lead_id)


@router.delete(
    "/leads/{lead_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    # См. комментарий в marketer_pricing.py: future-annotations + 204.
    response_model=None,
    summary="Удалить лида",
)
async def delete_lead(
    lead_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_LEADS_GATE),
) -> None:
    if not await lead_service.delete_lead(db, lead_id=lead_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Лид не найден")


@router.post(
    "/leads/{lead_id}/link",
    response_model=LeadRead,
    summary="Привязать лида к учётке ученика",
    description="Идемпотентно: повторная привязка того же ученика не ошибка.",
)
async def link_student(
    lead_id: int,
    body: LeadLinkRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_LEADS_GATE),
) -> LeadRead:
    # Привязать можно только действующего ученика. Иначе перебором номеров
    # карточка лида показывала бы ФИО кого угодно в школе — обход того самого
    # гейта персональных данных, ради которого общий поиск людей закрыт.
    if not await lead_service.is_linkable_student(db, body.student_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")

    try:
        ok = await lead_service.link_student(
            db, lead_id=lead_id, student_id=body.student_id
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден") from exc
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Лид не найден")
    return await _reload(db, lead_id)


@router.delete(
    "/leads/{lead_id}/link",
    response_model=LeadRead,
    summary="Снять привязку лида к ученику",
)
async def unlink_student(
    lead_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_LEADS_GATE),
) -> LeadRead:
    if not await lead_service.unlink_student(db, lead_id=lead_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Лид не найден")
    return await _reload(db, lead_id)


async def _reload(db: AsyncSession, lead_id: int) -> LeadRead:
    """Перечитать лида после записи.

    Отдельный хелпер вместо `assert`: под `python -O` проверки-`assert`
    исчезают, а это денежный контур — молчаливый `None` в ответе недопустим.
    """
    lead = await lead_service.get_lead(db, lead_id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Лид не найден")
    return lead


async def _validate_source(
    db: AsyncSession, source_id: int, source_detail: Optional[str]
) -> None:
    code = await lead_service.get_source_code(db, source_id)
    if code is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Канал не найден")
    if lead_service.requires_detail(code) and not (source_detail or "").strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Для канала «Другое» нужно указать, откуда именно пришёл лид",
        )
