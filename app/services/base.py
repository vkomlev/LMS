from __future__ import annotations

from typing import Any, Dict, Generic, List, Optional, TypeVar, Sequence, Mapping

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
    
    async def get_by_keys(
        self,
        db: AsyncSession,
        keys: dict[str, Any],
    ) -> ModelType | None:
        """
        Достаёт объект по ключам через репозиторий.
        """
        return await self.repo.get_by_keys(db, keys)

    async def update_by_keys(
        self,
        db: AsyncSession,
        keys: dict[str, Any],
        data: dict[str, Any],
    ) -> ModelType | None:
        """
        Обновляет объект по ключам через репозиторий.
        """
        return await self.repo.update_by_keys(db, keys, data)

    async def delete_by_keys(
        self,
        db: AsyncSession,
        keys: dict[str, Any],
    ) -> bool:
        """
        Удаляет объект по ключам через репозиторий.
        """
        return await self.repo.delete_by_keys(db, keys)


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

    async def search_text(
        self,
        db: AsyncSession,
        *,
        field: str | Sequence[str],
        query: str,
        mode: str = "contains",
        case_insensitive: bool = True,
        limit: int = 50,
        offset: int = 0,
        order_by=None,
    ) -> list[ModelType]:
        """
        Универсальный текстовый поиск по произвольным полям модели.
        """
        return await self.repo.search_text(
            db,
            field=field,
            query=query,
            mode=mode,
            case_insensitive=case_insensitive,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    # можно добавить ещё общие операции, например batch_create, delete_by_ids и т.п.

    async def patch(
        self,
        db: AsyncSession,
        db_obj: ModelType,
        fields: Mapping[str, Any],
    ) -> ModelType:
        """
        Частичное обновление: по умолчанию вызывает repo.update(...) или repo.update_fields(...).
        """
        # Если сделали repo.update_fields — можно звать её:
        # return await self.repo.update_fields(db, db_obj, fields)
        return await self.repo.update(db, db_obj, fields)  # если update уже частичный
