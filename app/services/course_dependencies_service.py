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
