"""API «поиск задания в кабинете преподавателя» (tsk-353).

``GET /api/v1/teacher/students/{student_id}/tasks/search?q=...``

На живом уроке ученик называет номер или содержание задания — преподавателю
нужно быстро найти его в контексте открытой карточки прогресса конкретного
ученика, не листая дерево курсов. Результат ведёт в уже существующую детальную
карточку (tsk-349, ``GET /teacher/students/{student_id}/tasks/{task_id}/history``) —
этот эндпоинт только помогает найти ``task_id``, правило проверки/эталон здесь
не отдаются.

Два режима одного ``q`` (см. ``task_search_service``): число/``id-<N>`` — точный
поиск по видимому номеру задания (tsk-309/311); иначе — полнотекстовый поиск по
условию/заголовку.

Гейт: роль ``teacher``/``methodist``/``admin`` плюс тот же scoped-ACL портала,
что у истории задания (``can_edit_progress``, tsk-297) — teacher видит только
задания своих учеников/закреплённых курсов, реально достижимые учеником.

Read-only: ни одной записи в БД.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, require_role
from app.auth.current_user import CurrentUser
from app.schemas.task_search import TaskSearchResponse, TaskSearchResult
from app.services import task_search_service

logger = logging.getLogger("api.teacher_task_search")

router = APIRouter(tags=["teacher_task_search"])

_GATE = require_role("teacher", "methodist", "admin")


@router.get(
    "/teacher/students/{student_id}/tasks/search",
    response_model=TaskSearchResponse,
    summary="Поиск задания в кабинете преподавателя (по номеру или тексту, в контексте ученика)",
)
async def search_student_tasks(
    student_id: int = Path(..., ge=1, description="ID ученика, в контексте которого ищем"),
    q: str = Query(
        ...,
        min_length=1,
        max_length=200,
        description="Номер задания (110 / id-110) или текст условия",
    ),
    limit: int = Query(20, ge=1, le=50, description="Максимум результатов"),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_GATE),
) -> TaskSearchResponse:
    """Найти задания, доступные преподавателю в контексте ученика.

    ACL и исключение заданий с ``course_id=NULL`` — в ``task_search_service``,
    он же выбирает режим поиска (номер vs текст) по содержимому ``q``.
    """
    results = await task_search_service.search_tasks_for_teacher(
        db, current_user=current_user, student_id=student_id, query=q, limit=limit,
    )
    return TaskSearchResponse(
        query=q, results=[TaskSearchResult(**r) for r in results]
    )
