"""
Learning Engine V1, этап 3.9: сводка нагрузки преподавателя.

GET /api/v1/teacher/workload?teacher_id=... — счётчики для главного экрана
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
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
    db: AsyncSession = Depends(get_db),
) -> TeacherWorkloadResponse:
    data = await get_teacher_workload(db, teacher_id)
    return TeacherWorkloadResponse(**data)
