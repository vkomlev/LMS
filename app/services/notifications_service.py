# app/services/notifications_service.py

from app.models.notifications import Notifications
from app.repos.notifications_repo import NotificationsRepository
from app.services.base import BaseService


class NotificationsService(BaseService[Notifications]):
    """
    Сервис для шаблонов уведомлений.
    """
    def __init__(self, repo: NotificationsRepository = NotificationsRepository()):
        super().__init__(repo)

    # TODO: list_recent
