# app/api/v1/course_dependencies.py

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.courses import CourseRead
from app.services.course_dependencies_service import CourseDependenciesService

router = APIRouter(
    prefix="/courses/{course_id}/dependencies",
    tags=["course_dependencies"],
)

service = CourseDependenciesService()


@router.get("/", response_model=List[CourseRead])
async def list_course_dependencies(
    course_id: int,
    db: AsyncSession = Depends(get_db),
) -> List[CourseRead]:
    """
    Получить все курсы, от которых зависит данный курс.
    """
    return await service.list_dependencies(db, course_id)


@router.post("/{required_course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def add_course_dependency(
    course_id: int,
    required_course_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Добавить зависимость: course_id зависит от required_course_id.
    """
    try:
        await service.add_dependency(db, course_id, required_course_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))


@router.delete("/{required_course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_course_dependency(
    course_id: int,
    required_course_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Удалить зависимость: course_id → required_course_id.
    """
    await service.remove_dependency(db, course_id, required_course_id)
