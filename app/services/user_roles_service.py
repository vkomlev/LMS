# app/services/user_roles_service.py
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.roles import Roles
from app.repos.user_roles import UserRolesRepository


class UserRolesService:
    """
    Сервис для назначения и отзыва ролей у пользователей.
    """
    def __init__(self, repo: UserRolesRepository = None):
        self.repo = repo or UserRolesRepository()

    async def list_roles(self, db: AsyncSession, user_id: int) -> List[Roles]:
        """
        Получить все роли, назначенные пользователю.
        """
        return await self.repo.list_roles(db, user_id)

    async def add_role(self, db: AsyncSession, user_id: int, role_id: int) -> None:
        """
        Добавить роль пользователю.
        Бросает ValueError, если user или role не найдены.
        """
        await self.repo.add_role(db, user_id, role_id)

    async def remove_role(self, db: AsyncSession, user_id: int, role_id: int) -> None:
        """
        Удалить роль у пользователя.
        Бросает ValueError, если user или role не найдены.
        """
        await self.repo.remove_role(db, user_id, role_id)
