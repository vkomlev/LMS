from typing import List
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.access_requests import AccessRequests
from app.repos.access_requests_repository import AccessRequestsRepository
from app.schemas.access_requests import AccessRequestCreate, AccessRequestUpdate
from app.services.base import BaseService

class AccessRequestsService (BaseService[AccessRequests]):
    """
    Сервис-обёртка вокруг AccessRequestsRepository.
    """
    def __init__(self, repo: AccessRequestsRepository | None = None):
        self.repo = repo or AccessRequestsRepository()

    
    async def list_by_flag(
        self, db: AsyncSession, flag: str
    ) -> List[AccessRequests]:
        return await self.repo.list_by_flag(db, flag)
