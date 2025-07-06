# app/services/courses_service.py

from app.models.courses import Courses
from app.repos.courses_repo import CoursesRepository
from app.services.base import BaseService


class CoursesService(BaseService[Courses]):
    """
    Сервис для курсов.
    """
    def __init__(self, repo: CoursesRepository = CoursesRepository()):
        super().__init__(repo)

    # TODO: методы для работы с иерархией, зависимостями и т.п.
