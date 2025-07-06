# app/repos/base.py

from typing import Any, Dict, Generic, List, Optional, Type, TypeVar
from sqlalchemy import delete, func, select, update, cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """
    Generic-репозиторий с расширенным CRUD:
      - базовые get/list/create/update/delete
      - фильтрация по полям
      - batch-операции
      - работа с JSONB: фильтрация и обновление ключей
    Наследники передают модель в конструктор и добавляют domain-specific методы.
    """

    def __init__(self, model: Type[ModelType]):
        self.model = model

    async def get(self, db: AsyncSession, id: Any) -> Optional[ModelType]:
        """Получить объект по первичному ключу."""
        return await db.get(self.model, id)

    async def list(
        self,
        db: AsyncSession,
        skip: int = 0,
        limit: int = 100
    ) -> List[ModelType]:
        """Список объектов с пагинацией."""
        stmt = select(self.model).offset(skip).limit(limit)
        res = await db.execute(stmt)
        return res.scalars().all()

    async def filter_by(
        self,
        db: AsyncSession,
        skip: int = 0,
        limit: int = 100,
        **filters: Any
    ) -> List[ModelType]:
        """
        Список объектов, отфильтрованных по полям.
        Пример: repo.filter_by(db, is_active=True, category='math')
        """
        stmt = select(self.model).filter_by(**filters).offset(skip).limit(limit)
        res = await db.execute(stmt)
        return res.scalars().all()

    async def batch_create(
        self,
        db: AsyncSession,
        objs_in: List[Dict[str, Any]]
    ) -> List[ModelType]:
        """
        Пакетное создание объектов.
        Добавляет все, коммитит, обновляет из БД и возвращает список новых сущностей.
        """
        objs = [self.model(**data) for data in objs_in]
        db.add_all(objs)
        await db.commit()
        for obj in objs:
            await db.refresh(obj)
        return objs

    async def delete_by_ids(
        self,
        db: AsyncSession,
        ids: List[Any]
    ) -> None:
        """
        Удалить несколько записей по списку первичных ключей.
        """
        stmt = delete(self.model).where(self.model.id.in_(ids))
        await db.execute(stmt)
        await db.commit()

    async def create(
        self,
        db: AsyncSession,
        obj_in: Dict[str, Any]
    ) -> ModelType:
        """Создать одну запись из словаря."""
        obj = self.model(**obj_in)
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        return obj

    async def update(
        self,
        db: AsyncSession,
        db_obj: ModelType,
        obj_in: Dict[str, Any]
    ) -> ModelType:
        """Обновить поля существующего объекта."""
        for field, value in obj_in.items():
            setattr(db_obj, field, value)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def delete(
        self,
        db: AsyncSession,
        db_obj: ModelType
    ) -> None:
        """Удалить одну запись (по объекту)."""
        await db.delete(db_obj)
        await db.commit()

    async def filter_by_json_key(
        self,
        db: AsyncSession,
        json_field: str,
        key_path: List[str],
        value: Any,
        skip: int = 0,
        limit: int = 100
    ) -> List[ModelType]:
        """
        Фильтрация по ключу в JSONB-поле.
        key_path — список вложенных ключей, например ['settings', 'theme'].
        """
        col = getattr(self.model, json_field)
        expr = col
        for key in key_path:
            expr = expr[key]
        # сравниваем текстовое значение; приводим value к строке
        stmt = select(self.model).where(expr.astext == str(value)).offset(skip).limit(limit)
        res = await db.execute(stmt)
        return res.scalars().all()

    async def update_json_field(
        self,
        db: AsyncSession,
        id: Any,
        json_field: str,
        key_path: List[str],
        value: Any
    ) -> Optional[ModelType]:
        """
        Обновить конкретный ключ в JSONB-поле одной записи.
        Возвращает обновлённый объект.
        """
        # формируем путь вида '{k1,k2,...}'
        path = "{" + ",".join(key_path) + "}"
        stmt = (
            update(self.model)
            .where(self.model.id == id)
            .values({
                json_field: func.jsonb_set(
                    getattr(self.model, json_field),
                    path,
                    cast(value, JSONB),
                    True
                )
            })
            .execution_options(synchronize_session="fetch")
        )
        await db.execute(stmt)
        await db.commit()
        return await self.get(db, id)
