# app/services/course_dependencies_service.py

from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courses import Courses
from app.repos.course_dependencies_repository import CourseDependenciesRepository
from app.services.learning_engine_service import LearningEngineService


class CourseDependenciesService:
    """
    Сервис для бизнес-логики работы с зависимостями курсов.
    """
    def __init__(
        self,
        repo: CourseDependenciesRepository = None,
        engine: Optional[LearningEngineService] = None,
    ):
        self.repo = repo or CourseDependenciesRepository()
        # tsk-541: пересчёт кеша student_course_state для узла, ставшего
        # required_course_id, сразу при записи зависимости — см. add_dependency.
        self.engine = engine or LearningEngineService()

    async def list_dependencies(
        self, db: AsyncSession, course_id: int
    ) -> List[Courses]:
        return await self.repo.list_dependencies(db, course_id)

    async def add_dependency(
        self, db: AsyncSession, course_id: int, required_course_id: int
    ) -> None:
        await self.repo.add_dependency(db, course_id, required_course_id)
        # tsk-541: без этого student_course_state для required_course_id не
        # пишет никто до следующего прогона фонового тика — активный студент,
        # уже прошедший пререквизит, видит новую зависимость как блокировку
        # (ровно регрессия tsk-523).
        await self.engine.backfill_dependency_state(db, course_id, required_course_id)
        await db.commit()

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
        added = await self.repo.bulk_add_dependencies(db, course_id, required_course_ids)
        # tsk-541: тот же бэкфилл, что в add_dependency — по одному пересчёту
        # на каждую реально добавленную зависимость (пропущенные конфликты/
        # self-dependency уже отфильтрованы репозиторием).
        for dep in added:
            await self.engine.backfill_dependency_state(db, course_id, dep.id)
        await db.commit()
        return added