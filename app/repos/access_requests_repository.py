# app/repos/access_requests.py
from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.access_requests import AccessRequests
from app.repos.base import BaseRepository


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
