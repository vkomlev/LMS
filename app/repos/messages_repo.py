# app/repos/messages_repo.py

from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.messages import Messages
from app.repos.base import BaseRepository


class MessagesRepository(BaseRepository[Messages]):
    """
    Репозиторий для сообщений.
    """
    def __init__(self) -> None:
        super().__init__(Messages)