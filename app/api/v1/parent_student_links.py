# app/api/v1/parent_student_links.py
"""
Связка родитель↔ученик (tsk-478, кабинет родителя). Паттерн — прямая копия
`student_teacher_links.py`: запись доступна ТОЛЬКО оператору/методисту/
админу, НЕ самому родителю (родитель не выбирает себе ученика).
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, require_role
from app.auth.current_user import CurrentUser
from app.schemas.users import UserRead
from app.services.parent_student_links_service import ParentStudentLinksService

router = APIRouter(tags=["parent_student_links"])
service = ParentStudentLinksService()

# Та же граница, что у student_teacher_links: кто видит/распределяет людей
# (методист, админ), тот и управляет связкой. Родитель сюда не допущен —
# связку создаёт оператор/преподаватель, не сам родитель.
_PEOPLE_WRITE_GATE = require_role("methodist", "admin")
_PEOPLE_READ_GATE = require_role("methodist", "admin")


@router.get(
    "/users/{student_id}/parents",
    response_model=List[UserRead],
    summary="Список родителей ученика",
)
async def list_student_parents(
    student_id: int,
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PEOPLE_READ_GATE),
) -> List[UserRead]:
    return await service.list_parents(db, student_id)


@router.post(
    "/users/{student_id}/parents/{parent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Привязать родителя к ученику",
    description=(
        "Создаёт связку родитель↔ученик и идемпотентно назначает роли "
        "`parent`, если она у пользователя ещё не назначена."
    ),
)
async def add_parent_student_link(
    student_id: int,
    parent_id: int,
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PEOPLE_WRITE_GATE),
) -> None:
    try:
        await service.add_link(db, parent_id, student_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))


@router.delete(
    "/users/{student_id}/parents/{parent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Отвязать родителя от ученика",
)
async def remove_parent_student_link(
    student_id: int,
    parent_id: int,
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PEOPLE_WRITE_GATE),
) -> None:
    await service.remove_link(db, parent_id, student_id)
