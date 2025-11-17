# app/api/v1/student_teacher_links.py

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.users import UserRead
from app.services.student_teacher_links_service import (
    StudentTeacherLinksService,
)

router = APIRouter(tags=["student_teacher_links"])
service = StudentTeacherLinksService()


# ---------- Студент → его преподаватели ----------

@router.get(
    "/users/{student_id}/teachers",
    response_model=List[UserRead],
    summary="Список преподавателей студента",
)
async def list_student_teachers(
    student_id: int,
    db: AsyncSession = Depends(get_db),
) -> List[UserRead]:
    """
    Вернуть всех преподавателей, привязанных к студенту `student_id`.
    """
    return await service.list_teachers(db, student_id)


@router.post(
    "/users/{student_id}/teachers/{teacher_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Добавить связь студент↔преподаватель",
)
async def add_student_teacher_link(
    student_id: int,
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Привязать преподавателя `teacher_id` к студенту `student_id`.

    Если один из пользователей не найден — 404.
    """
    try:
        await service.add_link(db, student_id, teacher_id)
    except ValueError as e:
        # Аналогично user_roles/course_dependencies: 404 на not found
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))


@router.delete(
    "/users/{student_id}/teachers/{teacher_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить связь студент↔преподаватель",
)
async def remove_student_teacher_link(
    student_id: int,
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Удалить связь между студентом и преподавателем.
    Если связи не было — просто вернём 204.
    """
    await service.remove_link(db, student_id, teacher_id)


# ---------- Преподаватель → его студенты ----------

@router.get(
    "/users/{teacher_id}/students",
    response_model=List[UserRead],
    summary="Список студентов преподавателя",
)
async def list_teacher_students(
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
) -> List[UserRead]:
    """
    Вернуть всех студентов, привязанных к преподавателю `teacher_id`.
    """
    return await service.list_students(db, teacher_id)
