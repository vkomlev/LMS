# app/services/course_dependencies_service.py

from typing import List
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courses import Courses
from app.repos.course_dependencies_repository import CourseDependenciesRepository


class CourseDependenciesService:
    """
    Сервис для бизнес-логики работы с зависимостями курсов.
    """
    def __init__(self, repo: CourseDependenciesRepository = None):
        self.repo = repo or CourseDependenciesRepository()

    async def list_dependencies(
        self, db: AsyncSession, course_id: int
    ) -> List[Courses]:
        return await self.repo.list_dependencies(db, course_id)

    async def add_dependency(
        self, db: AsyncSession, course_id: int, required_course_id: int
    ) -> None:
        await self.repo.add_dependency(db, course_id, required_course_id)

    async def remove_dependency(
        self, db: AsyncSession, course_id: int, required_course_id: int
    ) -> None:
        await self.repo.remove_dependency(db, course_id, required_course_id)

    async def bulk_add_dependencies(
        self, db: AsyncSession, course_id: int, required_course_ids: List[int]
    ) -> List[Courses]:
        """
        Массовое добавление зависимостей для курса.
        
        :param db: асинхронная сессия БД.
        :param course_id: ID курса.
        :param required_course_ids: Список ID курсов-зависимостей.
        :return: Список успешно добавленных зависимостей.
        """
        return await self.repo.bulk_add_dependencies(db, course_id, required_course_ids)