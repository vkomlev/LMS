from __future__ import annotations

from typing import Any, Optional
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attempts import Attempts
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

    async def finish_attempt(
        self,
        db: AsyncSession,
        attempt_id: int,
    ) -> Optional[Attempts]:
        """
        Помечает попытку как завершённую (проставляет finished_at).

        Если попытка не найдена, возвращает None.
        Отдельный уровень (эндпойнт) уже решит, бросать ли DomainError/HTTP 404.
        """
        attempt = await self.get_by_id(db, attempt_id)
        if attempt is None:
            return None

        update_data = {
            "finished_at": datetime.now(timezone.utc),
        }
        return await self.update(db, db_obj=attempt, obj_in=update_data)
