from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.courses import CourseRead
from app.services.courses_service import CoursesService

router = APIRouter(tags=["courses"])

courses_service = CoursesService()


@router.get(
    "/courses/by-code/{code}",
    response_model=CourseRead,
    summary="Получить курс по его коду (course_uid)",
)
async def get_course_by_code_endpoint(
    code: str,
    db: AsyncSession = Depends(get_db),
) -> CourseRead:
    """
    Вернуть курс по его внешнему коду (course_uid).

    Статусы:
    - 200 — если курс найден;
    - 404 — если курс не найден (DomainError обрабатывается глобальным хэндлером).
    """
    course = await courses_service.get_by_course_uid(db, course_uid=code)
    return course
