# app/services/users_service.py

from typing import Any, Dict, Optional, List, Tuple, Sequence
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.models.users import Users
from app.repos.users_repo import UsersRepository
from app.services.base import BaseService


class UsersService(BaseService[Users]):
    """
    Сервис для пользователей.
    """
    def __init__(self, repo: UsersRepository = UsersRepository()):
        super().__init__(repo)

    async def create(self, db: AsyncSession, obj_in: Dict[str, Any]) -> Users:
        """Создать пользователя.

        После M1 (2026-04-28) `password_hash` стал NULLABLE — пустая строка
        больше не подставляется. Тру-passwordless flow Y-1: для создаваемых
        users поле всегда `NULL`. Полное удаление колонки запланировано
        в M14 (tsk-004 этап 2).
        """
        return await self.repo.create(db, dict(obj_in))

    async def get_id_by_tg_id(self, db: AsyncSession, tg_id: int) -> int | None:
        """
        Вернуть integer id пользователя по tg_id, либо None.
        """
        user = await self.get_by_keys(db, {"tg_id": tg_id})
        return user.id if user else None

    async def list_with_role_filter(
        self,
        db: AsyncSession,
        *,
        role_name: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        order_by: Optional[Sequence[ColumnElement]] = None,
    ) -> Tuple[List[Users], int]:
        """
        Получить список пользователей с фильтрацией по роли.
        """
        return await self.repo.list_with_role_filter(
            db,
            role_name=role_name,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    async def search_by_full_name_with_role(
        self,
        db: AsyncSession,
        *,
        q: str,
        role_name: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Users]:
        """
        Поиск пользователей по full_name с опциональной фильтрацией по роли.
        Используется для эндпойнта `GET /api/v1/users/search`.
        """
        return await self.repo.search_by_full_name_with_role(
            db,
            q=q,
            role_name=role_name,
            limit=limit,
            offset=offset,
        )
