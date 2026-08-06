"""tsk-303 Поток B: обращения о проблемах системы/контента и идеи фич.

POST   /api/v1/feedback-reports          — создать обращение
GET    /api/v1/feedback-reports          — список (свои / все — по роли)
POST   /api/v1/feedback-reports/{id}/close — закрыть

Кто и что видит (решение оператора: инбокс обращений — у методиста/админа,
точка входа — у преподавателя):
- создать может преподаватель, методист или админ. Ученик — нет: этот поток
  про систему и контент, а не про его задание; для задания есть заявка помощи;
- методист и админ видят ВСЕ обращения — это их инбокс;
- преподаватель видит только свои: чужие жалобы на контент ему не адресованы.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, get_current_user
from app.auth.current_user import CurrentUser
from app.schemas.feedback_reports import (
    FeedbackReportCreateRequest,
    FeedbackReportCreateResponse,
    FeedbackReportItem,
    FeedbackReportListResponse,
    FeedbackReportCloseRequest,
    FeedbackReportCloseResponse,
)
from app.services import feedback_reports_service, roles_service

router = APIRouter(prefix="/feedback-reports", tags=["feedback_reports"])
logger = logging.getLogger("api.feedback_reports")

_AUTHOR_ROLES = {"teacher", "methodist", "admin"}
_INBOX_ROLES = {"methodist", "admin"}


async def _roles(db: AsyncSession, current_user: CurrentUser) -> set[str]:
    if current_user.is_service:
        return set(_AUTHOR_ROLES)
    return set(await roles_service.get_user_role_names(db, current_user.id))


@router.post(
    "",
    response_model=FeedbackReportCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать обращение о проблеме или идею фичи (tsk-303)",
    responses={
        201: {"description": "Обращение создано"},
        403: {"description": "Нет роли преподавателя/методиста/админа"},
        422: {"description": "Неизвестный тип или пустой текст"},
    },
)
async def create_feedback_report(
    body: FeedbackReportCreateRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> FeedbackReportCreateResponse:
    """Автор берётся из сессии — подставить чужого нельзя."""
    if not current_user.is_service and not (await _roles(db, current_user) & _AUTHOR_ROLES):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Обращения создают преподаватель, методист или админ",
        )
    data = await feedback_reports_service.create_report(
        db,
        author_id=current_user.id,
        report_type=body.report_type,
        body=body.body,
        course_id=body.course_id,
        material_id=body.material_id,
        task_id=body.task_id,
    )
    await db.commit()
    return FeedbackReportCreateResponse(**data)


@router.get(
    "",
    response_model=FeedbackReportListResponse,
    summary="Список обращений: свои или все (tsk-303)",
)
async def list_feedback_reports(
    status_filter: str = Query("open", description="open | closed | all", alias="status"),
    report_type: str = Query("all", description="bug | content | feature_idea | all"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> FeedbackReportListResponse:
    """Методист и админ видят все обращения, остальные — только свои.

    Сужение делается ЗДЕСЬ, параметром запроса к БД, а не фильтрацией уже
    выбранной страницы: постфильтр по узкому ключу поверх общего `limit` молча
    теряет строки (класс уже ловился в tsk-473).
    """
    roles = await _roles(db, current_user)
    is_inbox_viewer = current_user.is_service or bool(roles & _INBOX_ROLES)
    if not is_inbox_viewer and not (roles & _AUTHOR_ROLES):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к обращениям")

    items, total = await feedback_reports_service.list_reports(
        db,
        author_id=None if is_inbox_viewer else current_user.id,
        status_filter=status_filter,
        report_type=report_type,
        limit=limit,
        offset=offset,
    )
    return FeedbackReportListResponse(
        items=[FeedbackReportItem(**it) for it in items],
        total=total,
        scope="all" if is_inbox_viewer else "own",
    )


@router.post(
    "/{report_id}/close",
    response_model=FeedbackReportCloseResponse,
    summary="Закрыть обращение (tsk-303)",
    responses={
        404: {"description": "Обращение не найдено"},
        403: {"description": "Не автор и не методист/админ"},
    },
)
async def close_feedback_report(
    report_id: int = Path(..., description="ID обращения"),
    body: FeedbackReportCloseRequest = Body(default=FeedbackReportCloseRequest()),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> FeedbackReportCloseResponse:
    """Закрывает автор или методист/админ; повтор идемпотентен."""
    roles = await _roles(db, current_user)
    is_privileged = current_user.is_service or bool(roles & _INBOX_ROLES)
    data, err = await feedback_reports_service.close_report(
        db,
        report_id,
        current_user.id,
        body.resolution_comment,
        is_privileged=is_privileged,
    )
    if err == "not_found":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Обращение не найдено")
    if err == "forbidden":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Закрыть обращение может автор, методист или админ"
        )
    await db.commit()
    return FeedbackReportCloseResponse(**data)
