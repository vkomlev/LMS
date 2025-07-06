# app/services/study_plan_courses_service.py

from app.models.study_plan_courses import StudyPlanCourses
from app.repos.study_plan_courses_repo import StudyPlanCoursesRepository
from app.services.base import BaseService


class StudyPlanCoursesService(BaseService[StudyPlanCourses]):
    """
    Сервис для связей учебных планов и курсов.
    """
    def __init__(self, repo: StudyPlanCoursesRepository = StudyPlanCoursesRepository()):
        super().__init__(repo)

    # TODO: list_for_plan
