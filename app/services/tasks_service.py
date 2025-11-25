from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tasks import Tasks
from app.repos.tasks_repo import TasksRepository
from app.services.base import BaseService
from app.utils.exceptions import DomainError


class TasksService(BaseService[Tasks]):
    """
    Сервис для работы с заданиями.

    Базовый CRUD (create/get/update/delete/list/paginate) реализован
    в BaseService[Tasks]. Здесь добавляем доменные методы, связанные
    с импортом и внешним идентификатором.
    """

    def __init__(self, repo: TasksRepository = TasksRepository()) -> None:
        """
        Инициализирует сервис с репозиторием заданий.
        """
        super().__init__(repo)

    async def get_by_external_uid(
        self,
        db: AsyncSession,
        external_uid: str,
    ) -> Tasks:
        task = await self.repo.get_by_keys(
            db,
            {"external_uid": external_uid},
        )
        if task is None:
            raise DomainError(
                detail="Задача с указанным external_uid не найдена",
                status_code=404,
                payload={"external_uid": external_uid},
            )
        return task

