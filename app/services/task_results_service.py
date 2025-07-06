# app/services/task_results_service.py

from app.models.task_results import TaskResults
from app.repos.task_results_repo import TaskResultsRepository
from app.services.base import BaseService


class TaskResultsService(BaseService[TaskResults]):
    """
    Сервис для результатов выполнения заданий.
    """
    def __init__(self, repo: TaskResultsRepository = TaskResultsRepository()):
        super().__init__(repo)

    # TODO: list_for_task, list_for_user
