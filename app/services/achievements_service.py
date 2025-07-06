# app/services/achievements_service.py

from typing import Any, Dict, Optional
from app.models.achievements import Achievements
from app.repos.achievements_repo import AchievementsRepository
from app.services.base import BaseService


class AchievementsService(BaseService[Achievements]):
    """
    Сервис для достижений. Базовый CRUD уже в BaseService.
    Здесь добавляем только domain-specific методы.
    """
    def __init__(self, repo: AchievementsRepository = AchievementsRepository()):
        super().__init__(repo)

    async def get_by_name(
        self, db, name: str
    ) -> Optional[Achievements]:
        """Найти достижение по имени."""
        # пример: используем filter_by из BaseService
        results = await self.filter_by(db, name=name)
        return results[0] if results else None
