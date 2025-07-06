# app/services/user_achievements_service.py

from app.models.user_achievements import UserAchievements
from app.repos.user_achievements_repo import UserAchievementsRepository
from app.services.base import BaseService


class UserAchievementsService(BaseService[UserAchievements]):
    """
    Сервис для связей пользователей и достижений.
    """
    def __init__(self, repo: UserAchievementsRepository = UserAchievementsRepository()):
        super().__init__(repo)

    # TODO: list_for_user, list_for_achievement
