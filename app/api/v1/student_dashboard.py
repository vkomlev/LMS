"""
Периодный дашборд ученика (tsk-494) — основа для будущего кабинета родителя
(tsk-478) и текущих teacher/methodist/admin.

- `GET /students/{student_id}/dashboard?from=&to=` — курсы+прогресс+прогноз,
  итог за период, посещение, ДЗ между занятиями (см.
  `app/services/student_dashboard_service.py`).

Гейт — композитная проверка (tsk-478): `manual_progress_service.can_edit_progress`
(сервисный токен/admin/methodist — полный доступ, teacher — только свои
ученики) ИЛИ роль `parent` со связкой в `parent_student_links` на ЭТОГО
ученика. Родительская ветка НАМЕРЕННО не подмешана в
`can_edit_progress` — та функция используется по всему сервису и для
настоящего РЕДАКТИРОВАНИЯ прогресса; смешение создало бы риск скрытого
write-доступа родителю. Композиция — только здесь, в этом роуте.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user
from app.auth.current_user import CurrentUser
from app.schemas.student_dashboard import StudentDashboardRead
from app.services import manual_progress_service, roles_service, student_dashboard_service
from app.services.parent_student_links_service import ParentStudentLinksService

router = APIRouter(tags=["student_dashboard"])
_parent_links_service = ParentStudentLinksService()


async def _ensure_dashboard_access(
    db: AsyncSession, current_user: CurrentUser, student_id: int,
) -> bool:
    """Сервис/admin/methodist/teacher (через `can_edit_progress`) ИЛИ
    родитель, привязанный именно к этому ученику (`parent_student_links`).
    403 с общим текстом — не раскрывать вызывающему, какая из двух ветвей
    сработала бы при других правах.

    :returns: `True`, если доступ дан как персонал (первая ветка) — от этого
        зависит, увидит ли вызывающий норматив из цены (tsk-557, см.
        `viewer_is_staff` у `student_dashboard_service.get_student_dashboard`)."""
    if await manual_progress_service.can_edit_progress(db, current_user, student_id):
        return True
    if not current_user.is_service:
        roles = {r.lower().strip() for r in await roles_service.get_user_role_names(db, current_user.id)}
        if "parent" in roles and await _parent_links_service.is_linked(db, current_user.id, student_id):
            return False
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Доступ к дашборду этого ученика запрещён")


@router.get(
    "/students/{student_id}/dashboard",
    response_model=StudentDashboardRead,
)
async def get_student_dashboard(
    student_id: int,
    from_dt: datetime = Query(..., alias="from", description="Начало периода (включительно)"),
    to_dt: datetime = Query(..., alias="to", description="Конец периода (включительно)"),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> StudentDashboardRead:
    if from_dt.tzinfo is None or to_dt.tzinfo is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="from/to должны быть timezone-aware (ISO 8601 со смещением)",
        )
    if to_dt <= from_dt:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="to должен быть позже from")

    viewer_is_staff = await _ensure_dashboard_access(db, current_user, student_id)

    data = await student_dashboard_service.get_student_dashboard(
        db,
        student_id=student_id,
        period_from=from_dt,
        period_to=to_dt,
        viewer_is_staff=viewer_is_staff,
    )
    return StudentDashboardRead(**data)
