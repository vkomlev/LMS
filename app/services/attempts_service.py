from __future__ import annotations

from typing import Any, Optional
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attempts import Attempts
from app.models.task_results import TaskResults
from app.models.tasks import Tasks
from app.repos.attempts_repo import AttemptsRepository
from app.services.base import BaseService

class AttemptsService(BaseService[Attempts]):
    """
    Сервис для работы с попытками прохождения заданий.
    """
    def __init__(self, repo: AttemptsRepository = AttemptsRepository()):
        super().__init__(repo)

    async def create_attempt(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        course_id: Optional[int] = None,
        source_system: str = "system",
        meta: Optional[dict[str, Any]] = None,
    ) -> Attempts:
        """
        Создаёт попытку для пользователя (и, опционально, курса).

        :param db: Асинхронная сессия БД.
        :param user_id: ID пользователя.
        :param course_id: ID курса (если попытка привязана к курсу).
        :param source_system: Источник (web, bot, import и т.п.).
        :param meta: Дополнительные метаданные (таймлимит, название и пр.).
        """
        data: dict[str, Any] = {
            "user_id": user_id,
            "course_id": course_id,
            "source_system": source_system,
            "meta": meta,
        }
        # BaseService.create ожидает dict[str, Any]
        return await self.create(db, data)

    async def _get_task_ids_for_deadline_check(
        self,
        db: AsyncSession,
        attempt_id: int,
        course_id: Optional[int],
    ) -> list[int]:
        """ID задач для проверки дедлайна: из task_results попытки или по курсу."""
        r = await db.execute(
            select(TaskResults.task_id).where(TaskResults.attempt_id == attempt_id)
        )
        task_ids = [row[0] for row in r.fetchall()]
        if not task_ids and course_id is not None:
            r = await db.execute(
                select(Tasks.id).where(
                    Tasks.course_id == course_id,
                    Tasks.time_limit_sec.isnot(None),
                )
            )
            task_ids = [row[0] for row in r.fetchall()]
        return task_ids

    async def check_attempt_deadline_expired(
        self,
        db: AsyncSession,
        attempt: Attempts,
    ) -> bool:
        """
        True, если текущее время больше любого дедлайна по задачам попытки/курса
        (tasks.time_limit_sec). Используется в finish для выбора time_expired.
        """
        from app.services.tasks_service import TasksService
        now = datetime.now(timezone.utc)
        task_ids = await self._get_task_ids_for_deadline_check(db, attempt.id, attempt.course_id)
        tasks_svc = TasksService()
        for tid in task_ids:
            task = await tasks_svc.get_by_id(db, tid)
            if task and getattr(task, "time_limit_sec", None):
                deadline = attempt.created_at + timedelta(seconds=task.time_limit_sec)
                if now > deadline:
                    return True
        return False

    async def set_time_expired(
        self,
        db: AsyncSession,
        attempt_id: int,
    ) -> Optional[Attempts]:
        """
        Помечает попытку как просроченную (time_expired=true).
        Идемпотентно: повторный вызов не меняет состояние.
        """
        attempt = await self.get_by_id(db, attempt_id)
        if attempt is None:
            return None
        if attempt.time_expired:
            return attempt
        return await self.update(db, db_obj=attempt, obj_in={"time_expired": True})

    async def finish_attempt(
        self,
        db: AsyncSession,
        attempt_id: int,
        *,
        time_expired: bool = False,
    ) -> Optional[Attempts]:
        """
        Помечает попытку как завершённую (проставляет finished_at и при необходимости time_expired).

        Если попытка не найдена, возвращает None.
        Отдельный уровень (эндпойнт) уже решит, бросать ли DomainError/HTTP 404.
        """
        attempt = await self.get_by_id(db, attempt_id)
        if attempt is None:
            return None

        update_data: dict[str, Any] = {
            "finished_at": datetime.now(timezone.utc),
        }
        if time_expired:
            update_data["time_expired"] = True
        return await self.update(db, db_obj=attempt, obj_in=update_data)

    async def get_by_user(
        self,
        db: AsyncSession,
        user_id: int,
        course_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Attempts], int]:
        """
        Получить попытки пользователя с пагинацией.

        Args:
            db: Асинхронная сессия БД.
            user_id: ID пользователя.
            course_id: Опциональный фильтр по курсу.
            limit: Максимум записей на странице.
            offset: Смещение.

        Returns:
            Кортеж (список попыток, общее количество).
        """
        from sqlalchemy import desc

        filters = [self.repo.model.user_id == user_id]
        if course_id is not None:
            filters.append(self.repo.model.course_id == course_id)

        return await self.paginate(
            db,
            limit=limit,
            offset=offset,
            filters=filters,
            order_by=[desc(self.repo.model.created_at)],
        )