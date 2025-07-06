# app/repos/roles_repo.py

from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.roles import Roles
from app.repos.base import BaseRepository


class RolesRepository(BaseRepository[Roles]):
    """
    Репозиторий для ролей пользователей.
    """
    def __init__(self) -> None:
        super().__init__(Roles)