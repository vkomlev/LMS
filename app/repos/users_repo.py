from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.users import Users
from app.repos.base import BaseRepository

class UsersRepository(BaseRepository[Users]):
    """
    Репозиторий для пользователей.
    Помимо CRUD из BaseRepository, добавляем get_by_tg_id.
    """
    def __init__(self) -> None:
        super().__init__(Users)

    async def get_by_tg_id(
        self,
        db: AsyncSession,
        tg_id: int
    ) -> Optional[Users]:
        """Найти пользователя по Telegram ID."""
        q = select(self.model).where(self.model.tg_id == tg_id)
        result = await db.execute(q)
        return result.scalars().first()
