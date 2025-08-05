from typing import List
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.access_requests import AccessRequests
from app.repos.access_requests_repository import AccessRequestsRepository
from app.schemas.access_requests import AccessRequestCreate, AccessRequestUpdate, AccessRequestReadDetailed
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
        
    async def list_detailed_by_flag(
            self, db: AsyncSession, flag: str
        ) -> list[AccessRequestReadDetailed]:
            rows = await self.repo.list_detailed_by_flag(db, flag)
            detailed = []
            for ar, full_name, role_name in rows:
                detailed.append(
                    AccessRequestReadDetailed(
                        id=ar.id,
                        user_id=ar.user_id,
                        role_id=ar.role_id,
                        flag=ar.flag,
                        requested_at=ar.requested_at,
                        user_full_name=full_name,
                        role_name=role_name,
                    )
                )
            return detailed

