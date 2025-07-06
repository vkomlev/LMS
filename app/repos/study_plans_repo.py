# app/repos/study_plans_repo.py

from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.study_plans import StudyPlans
from app.repos.base import BaseRepository


class StudyPlansRepository(BaseRepository[StudyPlans]):
    """
    Репозиторий для учебных планов.
    """
    def __init__(self) -> None:
        super().__init__(StudyPlans)