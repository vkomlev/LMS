# app/repos/tasks_repo.py

from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tasks import Tasks
from app.repos.base import BaseRepository


class TasksRepository(BaseRepository[Tasks]):
    """
    Репозиторий для заданий.
    """
    def __init__(self) -> None:
        super().__init__(Tasks)