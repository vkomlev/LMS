from __future__ import annotations

from typing import Optional, Any, Dict, List, Sequence, Tuple

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
    
    async def bulk_upsert(
        self,
        db: AsyncSession,
        items: Sequence[Dict[str, Any]],
    ) -> List[Tuple[str, str, int]]:
        """
        Массовый upsert задач по external_uid.

        Для каждого элемента:
        - если задача с таким external_uid не найдена → создаём (CREATE),
        - если найдена → обновляем поля (UPDATE).

        :param db: асинхронная сессия БД.
        :param items: список словарей с полями задачи
                      (external_uid, course_id, difficulty_id, task_content,
                       solution_rules, max_score).
        :return: список кортежей (external_uid, action, id), где
                 action ∈ {"created", "updated"}.
        """
        results: List[Tuple[str, str, int]] = []

        for data in items:
            external_uid = data["external_uid"]

            # Пытаемся найти существующую задачу по external_uid
            existing = await self.repo.get_by_keys(
                db,
                {"external_uid": external_uid},
            )

            if existing is None:
                # CREATE
                obj_in = {
                    "external_uid": external_uid,
                    "course_id": data["course_id"],
                    "difficulty_id": data["difficulty_id"],
                    "task_content": data["task_content"],
                    "solution_rules": data.get("solution_rules"),
                    "max_score": data.get("max_score"),
                }
                # используем базовый репозиторий для создания
                task = await self.repo.create(db, obj_in)
                results.append((external_uid, "created", task.id))
            else:
                # UPDATE — перезаписываем основные поля из импорта
                existing.course_id = data["course_id"]
                existing.difficulty_id = data["difficulty_id"]
                existing.task_content = data["task_content"]
                existing.solution_rules = data.get("solution_rules")
                existing.max_score = data.get("max_score")

                db.add(existing)
                await db.commit()
                await db.refresh(existing)

                results.append((external_uid, "updated", existing.id))

        return results

