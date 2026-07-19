"""
Learning Engine V1, этап 3.9: сводка нагрузки преподавателя.

GET /api/v1/teacher/workload?teacher_id=... — счётчики для главного экрана
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, get_current_user
from app.auth.current_user import CurrentUser
from app.schemas.teacher_next_modes import TeacherWorkloadResponse
from app.services.teacher_queue_service import get_teacher_workload

router = APIRouter(prefix="/teacher", tags=["teacher_workload"])
logger = logging.getLogger("api.teacher_workload")


@router.get(
    "/workload",
    response_model=TeacherWorkloadResponse,
    summary="Сводка нагрузки преподавателя (этап 3.9)",
)
async def teacher_workload(
    teacher_id: int = Query(..., description="ID преподавателя"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> TeacherWorkloadResponse:
    """Сводка нагрузки преподавателя.

    tsk-298 Фаза 3: переведён с сервисного ключа (`get_db`) на
    `get_current_user` + identity-гейт — теперь доступен и веб-порталу
    преподавателя по cookie (не только TG-боту по service-key). Сервисный
    токен (`?api_key=` / X-API-Key) по-прежнему проходит (bypass).
    """
    if not current_user.is_service and current_user.id != teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    data = await get_teacher_workload(db, teacher_id)
    return TeacherWorkloadResponse(**data)
