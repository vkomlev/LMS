# app/services/tasks_service.py

from app.models.tasks import Tasks
from app.repos.tasks_repo import TasksRepository
from app.services.base import BaseService


class TasksService(BaseService[Tasks]):
    """
    Сервис для заданий.
    """
    def __init__(self, repo: TasksRepository = TasksRepository()):
        super().__init__(repo)

    # TODO: list_by_course
