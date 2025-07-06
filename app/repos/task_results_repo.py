# app/repos/task_results_repo.py

from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task_results import TaskResults
from app.repos.base import BaseRepository


class TaskResultsRepository(BaseRepository[TaskResults]):
    """
    Репозиторий для результатов выполнения заданий.
    """
    def __init__(self) -> None:
        super().__init__(TaskResults)