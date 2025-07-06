# app/services/users_service.py

from typing import Any, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.users import Users
from app.repos.users_repo import UsersRepository
from app.services.base import BaseService


class UsersService(BaseService[Users]):
    """
    Сервис для пользователей.
    """
    def __init__(self, repo: UsersRepository = UsersRepository()):
        super().__init__(repo)

    async def get_by_tg_id(
        self, db: AsyncSession, tg_id: int
    ) -> Optional[Users]:
        """Найти пользователя по Telegram ID."""
        return await self.repo.get_by_tg_id(db, tg_id)
