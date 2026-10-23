"""tsk-997: учебные достижения — экран ученика и блок в карточке у преподавателя.

- ``GET /me/achievements`` — ученик видит свои достижения и ближайшие с
  прогрессом. Рубильник ``ACHIEVEMENTS_STUDENT_UI_ENABLED``.
- ``GET /teacher/students/{student_id}/achievements`` — преподаватель видит
  значки своего ученика (только чтение, тот же ACL, что у правки прогресса).
  Рубильник ``ACHIEVEMENTS_TEACHER_UI_ENABLED``.

Оба рубильника по умолчанию выключены: при выключенном отвечаем
``enabled: false`` с пустыми списками (не 404 — клиенту не нужно отличать
«выключено» от «сломано»). Начисление в фоне от рубильников не зависит.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_bare_db, require_authenticated, require_role
from app.auth.current_user import CurrentUser
from app.core.config import Settings
from app.schemas.achievements_student import StudentAchievementsRead
from app.services import manual_progress_service, student_achievements_service

logger = logging.getLogger("api.student_achievements")

router = APIRouter(tags=["achievements"])


@router.get(
    "/me/achievements",
    response_model=StudentAchievementsRead,
    summary="Мои учебные достижения",
)
async def get_my_achievements(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> StudentAchievementsRead:
    """Достижения за усилие текущего ученика (без сравнения с другими)."""
    if not Settings().achievements_student_ui_enabled:
        return StudentAchievementsRead(enabled=False)
    data = await student_achievements_service.get_student_achievements(db, student_id=current_user.id)
    return StudentAchievementsRead(enabled=True, **data)


@router.get(
    "/teacher/students/{student_id}/achievements",
    response_model=StudentAchievementsRead,
    summary="Учебные достижения ученика (для преподавателя)",
)
async def get_student_achievements(
    student_id: int = Path(..., ge=1, description="ID ученика"),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(require_role("teacher", "methodist", "admin")),
) -> StudentAchievementsRead:
    """Значки ученика в его карточке: сигнал «возвращается к ошибкам»."""
    if not Settings().achievements_teacher_ui_enabled:
        return StudentAchievementsRead(enabled=False)
    await manual_progress_service.ensure_can_edit_progress(db, current_user, student_id)
    data = await student_achievements_service.get_student_achievements(db, student_id=student_id)
    return StudentAchievementsRead(enabled=True, **data)
