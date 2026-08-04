"""
Ссылки доступа родителя к дашборду ученика без регистрации (tsk-498).

Два контура с РАЗНЫМИ гейтами, поэтому два роутера в одном файле:

1. Управление (`router`) — выдача/список/отзыв, гейт
   `require_role("methodist","admin")`. Тот же круг, что управляет связками
   людей (`student_teacher_links`/`parent_student_links`).
2. Публичный (`public_router`) — открытие дашборда по токену, БЕЗ auth вообще:
   в этом весь смысл задачи (родитель не регистрируется). Токен и есть пропуск.

Почему это не дыра шире, чем задумано: токен открывает РОВНО этот один
read-only эндпоинт для РОВНО одного ученика и не является сессией — войти по
нему в LMS под чьей-либо учёткой нельзя. Несуществующий и отозванный токен
неотличимы (оба 404) — ответ не подтверждает, что токен когда-либо был.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.core.config import Settings
from app.models.parent_access_link import ParentAccessLink
from app.schemas.parent_access_link import (
    ParentAccessLinkCreatedRead,
    ParentAccessLinkCreateRequest,
    ParentAccessLinkRead,
    PublicParentDashboardRead,
)
from app.services import parent_access_link_service, student_dashboard_service

router = APIRouter(tags=["parent_access_links"])
public_router = APIRouter(tags=["parent_access_links_public"])

_LINKS_GATE = require_role("methodist", "admin")

#: Период дашборда по умолчанию, если ссылку открыли без явных дат.
_DEFAULT_PERIOD_DAYS = 30


def _to_read(link: ParentAccessLink) -> ParentAccessLinkRead:
    return ParentAccessLinkRead(
        id=link.id,
        student_id=link.student_id,
        label=link.label,
        created_at=link.created_at,
        revoked_at=link.revoked_at,
        last_used_at=link.last_used_at,
        is_active=link.revoked_at is None,
    )


@router.post(
    "/students/{student_id}/access-links",
    response_model=ParentAccessLinkCreatedRead,
    status_code=status.HTTP_201_CREATED,
    summary="Выдать родительскую ссылку на дашборд ученика",
    description=(
        "Возвращает готовую ссылку и сам токен — ЕДИНСТВЕННЫЙ раз. В базе "
        "хранится только хеш: показать эту же ссылку повторно нельзя, можно "
        "лишь выпустить новую. Срока годности нет, гасится ручным отзывом."
    ),
)
async def create_access_link(
    student_id: int,
    body: ParentAccessLinkCreateRequest | None = None,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_LINKS_GATE),
) -> ParentAccessLinkCreatedRead:
    exists = (
        await db.execute(text("SELECT 1 FROM users WHERE id = :id"), {"id": student_id})
    ).first()
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")

    link, raw_token = await parent_access_link_service.create_link(
        db,
        student_id=student_id,
        label=(body.label if body is not None else None),
        created_by_user_id=None if current_user.is_service else current_user.id,
    )
    base = Settings().public_base_url.rstrip("/")
    return ParentAccessLinkCreatedRead(
        **_to_read(link).model_dump(),
        token=raw_token,
        url=f"{base}/p/{raw_token}",
    )


@router.get(
    "/students/{student_id}/access-links",
    response_model=list[ParentAccessLinkRead],
    summary="Родительские ссылки ученика",
    description=(
        "История выдач, включая отозванные. Самих ссылок здесь нет — только "
        "их метки и состояние (см. описание создания)."
    ),
)
async def list_access_links(
    student_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_LINKS_GATE),
) -> list[ParentAccessLinkRead]:
    links = await parent_access_link_service.list_links(db, student_id=student_id)
    return [_to_read(link) for link in links]


@router.delete(
    "/parent-access-links/{link_id}",
    response_model=ParentAccessLinkRead,
    summary="Отозвать родительскую ссылку",
    description="Идемпотентно: повторный отзыв не ошибка, время первого сохраняется.",
)
async def revoke_access_link(
    link_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_LINKS_GATE),
) -> ParentAccessLinkRead:
    link = await parent_access_link_service.revoke_link(db, link_id=link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ссылка не найдена")
    return _to_read(link)


@public_router.get(
    "/public/parent-dashboard/{token}",
    response_model=PublicParentDashboardRead,
    summary="Дашборд ученика по родительской ссылке (без авторизации)",
    description=(
        "Открывается по токену из ссылки, которую выдал оператор. Отозванный "
        "и несуществующий токен неотличимы — оба 404."
    ),
)
async def get_dashboard_by_token(
    token: str = Path(..., min_length=8, max_length=128),
    from_dt: Optional[datetime] = Query(default=None, alias="from"),
    to_dt: Optional[datetime] = Query(default=None, alias="to"),
    db: AsyncSession = Depends(get_async_db),
) -> PublicParentDashboardRead:
    link = await parent_access_link_service.resolve_token(db, token)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ссылка недействительна")

    # Период необязателен: ссылку могут открыть «голой», и она обязана
    # работать без параметров — иначе родитель увидит ошибку вместо дашборда.
    now = datetime.now(timezone.utc)
    period_to = to_dt or now
    period_from = from_dt or (period_to - timedelta(days=_DEFAULT_PERIOD_DAYS))
    if period_from.tzinfo is None or period_to.tzinfo is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="from/to должны быть timezone-aware (ISO 8601 со смещением)",
        )
    if period_to <= period_from:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="to должен быть позже from"
        )

    # `viewer_is_staff` не передаём — гостевая ссылка всегда родительская
    # (tsk-557): норматив из цены останется `None`.
    data = await student_dashboard_service.get_student_dashboard(
        db,
        student_id=link.student_id,
        period_from=period_from,
        period_to=period_to,
    )
    full_name = (
        await db.execute(
            text("SELECT full_name FROM users WHERE id = :id"), {"id": link.student_id}
        )
    ).scalar()

    await parent_access_link_service.touch_last_used(db, link)
    return PublicParentDashboardRead(**data, student_full_name=full_name)
