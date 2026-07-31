"""
Периодный дашборд ученика (tsk-494) — основа для будущего кабинета родителя
(tsk-478) и текущих teacher/methodist/admin.

- `GET /students/{student_id}/dashboard?from=&to=` — курсы+прогресс+прогноз,
  итог за период, посещение, ДЗ между занятиями (см.
  `app/services/student_dashboard_service.py`).

Гейт — `manual_progress_service.ensure_can_edit_progress` (тот же ACL, что у
`GET /teacher/students/{id}/...`-эндпоинтов: сервисный токен и
admin/methodist — полный доступ, teacher — только свои ученики). Родительский
ACL — НЕ здесь, добавляется в tsk-478 отдельным гейтом поверх того же сервиса.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user
from app.auth.current_user import CurrentUser
from app.schemas.student_dashboard import StudentDashboardRead
from app.services import manual_progress_service, student_dashboard_service

router = APIRouter(tags=["student_dashboard"])


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

    await manual_progress_service.ensure_can_edit_progress(db, current_user, student_id)

    data = await student_dashboard_service.get_student_dashboard(
        db,
        student_id=student_id,
        current_user=current_user,
        period_from=from_dt,
        period_to=to_dt,
    )
    return StudentDashboardRead(**data)
