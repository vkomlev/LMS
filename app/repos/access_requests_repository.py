# app/repos/access_requests.py
from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.access_requests import AccessRequests
from app.repos.base import BaseRepository
from app.models.users import Users
from app.models.roles import Roles


class AccessRequestsRepository(BaseRepository[AccessRequests]):
    model = AccessRequests
    def __init__(self) -> None:
        super().__init__(AccessRequests)

    async def list_by_flag(self, db: AsyncSession, flag: str) -> List[AccessRequests]:
        """
        Вернуть все запросы с данным флагом, отсортированные по requested_at.
        """
        stmt = (
            select(self.model)
            .where(self.model.flag == flag)
            .order_by(self.model.requested_at)
        )
        res = await db.execute(stmt)
        return res.scalars().all()
    
    async def list_detailed_by_flag(self, db: AsyncSession, flag: str) -> list[tuple[AccessRequests, str, str]]:
        """
        Select AccessRequests + Users.full_name + Roles.name по заданному флагу.
        """
        stmt = (
            select(
                AccessRequests,
                Users.full_name.label("user_full_name"),
                Roles.name.label("role_name"),
            )
            .join(Users, AccessRequests.user_id == Users.id)
            .join(Roles, AccessRequests.role_id == Roles.id)
            .where(AccessRequests.flag == flag)
            .order_by(AccessRequests.requested_at)
        )
        result = await db.execute(stmt)
        return result.all()

