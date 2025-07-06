# app/repos/notifications_repo.py

from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notifications import Notifications
from app.repos.base import BaseRepository


class NotificationsRepository(BaseRepository[Notifications]):
    """
    Репозиторий для шаблонов уведомлений.
    """
    def __init__(self) -> None:
        super().__init__(Notifications)