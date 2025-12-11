from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attempts import Attempts
from app.repos.base import BaseRepository


class AttemptsRepository(BaseRepository[Attempts]):
    """
    Репозиторий для попыток прохождения заданий.
    """
    def __init__(self) -> None:
        super().__init__(Attempts)

    # При необходимости потом можно добавить методы:
    # async def list_for_user(self, db: AsyncSession, user_id: int) -> list[Attempts]: ...
