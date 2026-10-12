"""tsk-1322: где видеть действующие замки зависимостей курсов.

Замок «курс A ждёт прохождения курса B» (`course_dependencies`) раньше был виден
только ученику — фразой «сначала пройдите курс…». Ученица жила с замком два
месяца, пока не написала преподавателю. Отсюда два экрана:

- ``GET /teacher/students/{student_id}/dependency-locks`` — блок в карточке
  ученика: преподаватель видит замки по курсам, к которым у него есть доступ
  (тот же ACL, что у правки прогресса);
- ``GET /admin/dependency-locks`` — все замки школы для администратора.

Read-only: ни одной записи в БД.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, require_role
from app.auth.current_user import CurrentUser
from app.schemas.dependency_locks import AdminDependencyLockRead, DependencyLockRead
from app.services import dependency_lock_service, manual_progress_service

logger = logging.getLogger("api.dependency_locks")

router = APIRouter(tags=["dependency_locks"])


@router.get(
    "/teacher/students/{student_id}/dependency-locks",
    response_model=list[DependencyLockRead],
    summary="Действующие замки курсов ученика (для преподавателя)",
)
async def get_student_dependency_locks(
    student_id: int = Path(..., ge=1, description="ID ученика"),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(require_role("teacher", "methodist", "admin")),
) -> list[DependencyLockRead]:
    """Замки ученика по курсам, доступным текущему преподавателю.

    Замок по чужому курсу не показывается, а не даёт 403: карточка общая, и
    пустой блок честнее, чем ошибка на всей странице.
    """
    locks = await dependency_lock_service.describe_locks(db, student_id)
    visible = [
        lock for lock in locks
        if await manual_progress_service.can_edit_progress(
            db, current_user, student_id, lock["course_id"]
        )
    ]
    return [DependencyLockRead(**lock) for lock in visible]


@router.get(
    "/admin/dependency-locks",
    response_model=list[AdminDependencyLockRead],
    summary="Все действующие замки курсов (для администратора)",
)
async def list_dependency_locks(
    db: AsyncSession = Depends(get_bare_db),
    _current_user: CurrentUser = Depends(require_role("admin")),
) -> list[AdminDependencyLockRead]:
    """Все замки школы: кто, какой курс закрыт, чем и с какой даты."""
    locks = await dependency_lock_service.list_all_locks(db)
    return [AdminDependencyLockRead(**lock) for lock in locks]
