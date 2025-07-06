# app/repos/courses_repo.py

from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courses import Courses
from app.repos.base import BaseRepository


class CoursesRepository(BaseRepository[Courses]):
    """
    Репозиторий для курсов.
    Добавляйте здесь методы-спецы: иерархия, зависимости, и т.п.
    """
    def __init__(self) -> None:
        super().__init__(Courses)