# app/repos/user_achievements_repo.py

from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_achievements import UserAchievements
from app.repos.base import BaseRepository


class UserAchievementsRepository(BaseRepository[UserAchievements]):
    """
    Репозиторий для связей пользователей и их достижений.
    """
    def __init__(self) -> None:
        super().__init__(UserAchievements)