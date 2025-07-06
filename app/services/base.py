from typing import Any, Dict, Generic, List, Optional, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.repos.base import BaseRepository

ModelType = TypeVar("ModelType", bound=Base)


class BaseService(Generic[ModelType]):
    """
    Generic-сервис: минимальный CRUD + общие операции, делегируя BaseRepository.
    Конкретные сервисы наследуют его и добавляют domain-specific методы.
    """

    def __init__(self, repo: BaseRepository[ModelType]):
        """
        :param repo: репозиторий, инстанс BaseRepository[ModelType]
        """
        self.repo = repo

    async def get_by_id(
        self, db: AsyncSession, id: Any
    ) -> Optional[ModelType]:
        """Получить по PK."""
        return await self.repo.get(db, id)

    async def list(
        self, db: AsyncSession, skip: int = 0, limit: int = 100
    ) -> List[ModelType]:
        """Получить список."""
        return await self.repo.list(db, skip, limit)

    async def filter_by(
        self, db: AsyncSession, skip: int = 0, limit: int = 100, **filters: Any
    ) -> List[ModelType]:
        """Фильтрация по полям."""
        return await self.repo.filter_by(db, skip=skip, limit=limit, **filters)

    async def create(
        self, db: AsyncSession, obj_in: Dict[str, Any]
    ) -> ModelType:
        """Создать запись."""
        return await self.repo.create(db, obj_in)

    async def update(
        self, db: AsyncSession, db_obj: ModelType, obj_in: Dict[str, Any]
    ) -> ModelType:
        """Обновить запись."""
        return await self.repo.update(db, db_obj, obj_in)

    async def delete(
        self, db: AsyncSession, db_obj: ModelType
    ) -> None:
        """Удалить запись."""
        await self.repo.delete(db, db_obj)

    # можно добавить ещё общие операции, например batch_create, delete_by_ids и т.п.
