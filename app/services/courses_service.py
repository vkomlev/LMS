# app/services/courses_service.py

from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courses import Courses
from app.repos.courses_repo import CoursesRepository
from app.services.base import BaseService
from app.utils.exceptions import DomainError


class CoursesService(BaseService[Courses]):
    """
    Сервис для работы с курсами.

    Базовый CRUD реализован в BaseService[Courses].
    Здесь добавляем доменные методы, связанные с импортом и внешним кодом курса.
    """

    def __init__(self, repo: CoursesRepository = CoursesRepository()) -> None:
        super().__init__(repo)

    async def get_by_course_uid(
        self,
        db: AsyncSession,
        course_uid: str,
    ) -> Courses:
        """
        Найти курс по его внешнему коду (course_uid).

        :param db: асинхронная сессия БД.
        :param course_uid: внешний код курса (например, 'COURSE-PY-01').
        :return: ORM-объект Courses.
        :raises DomainError: если курс с таким course_uid не найден.
        """
        course: Optional[Courses] = await self.repo.get_by_keys(
            db,
            {"course_uid": course_uid},
        )
        if course is None:
            raise DomainError(
                detail="Курс с указанным кодом не найден",
                status_code=404,
                payload={"course_uid": course_uid},
            )
        return course
