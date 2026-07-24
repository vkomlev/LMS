"""API «история выполнения задания» для преподавателя (tsk-349).

``GET /api/v1/teacher/students/{student_id}/tasks/{task_id}/history``

Отдаёт всё по паре (ученик, задание): попытки с комментариями преподавателя,
заявки помощи с диалогом, подсказки, полное условие задания И правило проверки /
эталонный ответ (блок ``solution``) — учителю негде было увидеть эталон, кроме
прямого запроса к БД.

Гейт: роль ``teacher`` / ``methodist`` / ``admin`` (или сервисный токен) плюс
scoped-ACL портала (тот же, что у правки прогресса, tsk-297): teacher видит
историю только своих учеников или учеников на закреплённых за ним курсах.

Read-only: ни одной записи в БД.

Строительный блок для поиска задания в кабинете преподавателя (tsk-353) — карточка
не привязана к экрану прогресса, тот же эндпоинт питает результат поиска.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, require_role
from app.auth.current_user import CurrentUser
from app.schemas.task_history import TaskHistoryResponse
from app.services import manual_progress_service, task_history_service

logger = logging.getLogger("api.teacher_task_history")

router = APIRouter(tags=["teacher_task_history"])

_GATE = require_role("teacher", "methodist", "admin")


@router.get(
    "/teacher/students/{student_id}/tasks/{task_id}/history",
    response_model=TaskHistoryResponse,
    summary="История выполнения задания учеником (для преподавателя)",
)
async def get_task_history_for_teacher(
    student_id: int = Path(..., ge=1, description="ID ученика"),
    task_id: int = Path(..., ge=1, description="ID задания"),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_GATE),
) -> TaskHistoryResponse:
    """Полная история по заданию + условие + правило проверки/эталон.

    ACL до сборки данных: курс задания резолвится первым, доступ проверяется тем
    же ACL портала, что и правка прогресса. Ответ содержит блок ``solution`` —
    эталон отдаётся только этому (преподавательскому) эндпоинту.
    """
    course_id = await task_history_service.course_of_task(db, task_id)
    if course_id is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Задание {task_id} не найдено"
        )

    if not await manual_progress_service.can_edit_progress(
        db, current_user, student_id, course_id
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=(
                "Смотреть историю ученика может преподаватель — только своих "
                "учеников или учеников на закреплённых за ним курсах; полный "
                "доступ у методиста и админа"
            ),
        )

    data = await task_history_service.build_task_history(
        db, user_id=student_id, task_id=task_id, include_solution=True
    )
    if data is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Задание {task_id} не найдено"
        )
    return TaskHistoryResponse(**data)
