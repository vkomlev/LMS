# app/repos/achievements_repo.py

from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.achievements import Achievements
from app.repos.base import BaseRepository


class AchievementsRepository(BaseRepository[Achievements]):
    """
    Репозиторий для достижений.
    Здесь можно добавить методы типа get_by_name, фильтрацию по условию и т.п.
    """
    def __init__(self) -> None:
        super().__init__(Achievements)