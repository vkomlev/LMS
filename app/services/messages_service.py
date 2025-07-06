# app/services/messages_service.py

from app.models.messages import Messages
from app.repos.messages_repo import MessagesRepository
from app.services.base import BaseService


class MessagesService(BaseService[Messages]):
    """
    Сервис для сообщений.
    """
    def __init__(self, repo: MessagesRepository = MessagesRepository()):
        super().__init__(repo)

    # TODO: list_for_user
