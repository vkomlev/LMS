# app/repos/materials_repo.py

from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.materials import Materials
from app.repos.base import BaseRepository


class MaterialsRepository(BaseRepository[Materials]):
    """
    Репозиторий для учебных материалов.
    """
    def __init__(self) -> None:
        super().__init__(Materials)