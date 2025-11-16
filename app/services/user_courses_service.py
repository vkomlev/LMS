# app/services/study_plan_courses_service.py

from app.models.user_courses import UserCourses
from app.repos.user_courses_repo import UserCoursesRepository
from app.services.base import BaseService


class UserCoursesService(BaseService[UserCourses]):
    """
    Сервис для связей пользователей с курсами.
    """
    def __init__(self, repo: UserCoursesRepository = UserCoursesRepository()):
        super().__init__(repo)
