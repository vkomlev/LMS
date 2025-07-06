# app/repos/study_plan_courses_repo.py

from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.study_plan_courses import StudyPlanCourses
from app.repos.base import BaseRepository


class StudyPlanCoursesRepository(BaseRepository[StudyPlanCourses]):
    """
    Репозиторий для связей учебных планов и курсов.
    """
    def __init__(self) -> None:
        super().__init__(StudyPlanCourses)