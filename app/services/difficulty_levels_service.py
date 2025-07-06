# app/services/difficulty_levels_service.py

from app.models.difficulty_levels import DifficultyLevels
from app.repos.difficulty_levels_repo import DifficultyLevelsRepository
from app.services.base import BaseService


class DifficultyLevelsService(BaseService[DifficultyLevels]):
    """
    Сервис для уровней сложности.
    """
    def __init__(self, repo: DifficultyLevelsRepository = DifficultyLevelsRepository()):
        super().__init__(repo)

    # TODO: get_by_weight и др.
