# app/repos/difficulty_levels_repo.py

from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.difficulty_levels import DifficultyLevels
from app.repos.base import BaseRepository


class DifficultyLevelsRepository(BaseRepository[DifficultyLevels]):
    """
    Репозиторий для уровней сложности.
    """
    def __init__(self) -> None:
        super().__init__(DifficultyLevels)