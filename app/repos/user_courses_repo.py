# app/repos/study_plan_courses_repo.py

from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_courses import UserCourses
from app.repos.base import BaseRepository


class UserCoursesRepository(BaseRepository[UserCourses]):
    """
    Репозиторий для связей пользователей с курсами.
    """
    def __init__(self) -> None:
        super().__init__(UserCourses)