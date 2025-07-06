# app/services/study_plans_service.py

from app.models.study_plans import StudyPlans
from app.repos.study_plans_repo import StudyPlansRepository
from app.services.base import BaseService


class StudyPlansService(BaseService[StudyPlans]):
    """
    Сервис для учебных планов.
    """
    def __init__(self, repo: StudyPlansRepository = StudyPlansRepository()):
        super().__init__(repo)

    # TODO: list_active_for_user
