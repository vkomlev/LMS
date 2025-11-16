# app/repos/base.py

from typing import Any, Dict, Generic, List, Optional, Type, TypeVar, Iterable, Sequence, Mapping, Tuple
from sqlalchemy import delete, func, select, update, cast, or_, inspect, Select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql import ColumnElement
from sqlalchemy.sql.sqltypes import String, Text
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
    protected_update_fields: set[str] = set()  # можно переопределить в наследниках

    def __init__(self, model: Type[ModelType]):
        self.model = model

    async def get(self, db: AsyncSession, id: Any) -> Optional[ModelType]:
        """Получить объект по первичному ключу."""
        return await db.get(self.model, id)
    
    async def get_by_keys(
        self,
        db: AsyncSession,
        keys: dict[str, Any],
    ) -> ModelType | None:
        """
        Универсальный поиск по составному или одиночному ключу.
        `keys` — словарь вида {'field1': value1, 'field2': value2, …}.
        """
        stmt = select(self.model).filter_by(**keys)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def update_by_keys(
        self,
        db: AsyncSession,
        keys: dict[str, Any],
        data: dict[str, Any],
    ) -> ModelType | None:
        """
        Обновление записи, найденной по ключам.
        Возвращает обновлённый объект или None, если не найден.
        """
        obj = await self.get_by_keys(db, keys)
        if not obj:
            return None
        for field, value in data.items():
            setattr(obj, field, value)
        await db.commit()
        await db.refresh(obj)
        return obj

    async def delete_by_keys(
        self,
        db: AsyncSession,
        keys: dict[str, Any],
    ) -> bool:
        """
        Удаляет запись по ключам.
        Возвращает True, если удалил, False, если не нашёл.
        """
        obj = await self.get_by_keys(db, keys)
        if not obj:
            return False
        await db.delete(obj)
        await db.commit()
        return True

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

    async def search_text(
        self,
        db: AsyncSession,
        *,
        field: str | InstrumentedAttribute | Sequence[str],
        query: str,
        mode: str = "contains",         # "contains" | "prefix" | "suffix" | "exact"
        case_insensitive: bool = True,
        limit: int = 50,
        offset: int = 0,
        order_by: InstrumentedAttribute | None = None,
    ) -> List[ModelType]:
        """
        Универсальный LIKE/ILIKE-поиск по одному или нескольким текстовым полям модели.

        :param field: имя поля, сам столбец (InstrumentedAttribute) или список имён полей
        :param query: фрагмент строки для поиска
        :param mode: стратегия сопоставления: contains/prefix/suffix/exact
        :param case_insensitive: использовать ILIKE (PostgreSQL) вместо LIKE
        :param limit: лимит результатов
        :param offset: смещение
        :param order_by: столбец для сортировки
        """
        if not query:
            return []

        def _as_columns(f: str | InstrumentedAttribute | Sequence[str]) -> list[InstrumentedAttribute]:
            if isinstance(f, (list, tuple)):
                cols: list[InstrumentedAttribute] = []
                for name in f:
                    col = getattr(self.model, name, None)
                    if col is None:
                        raise ValueError(f"Column '{name}' not found on {self.model.__name__}")
                    cols.append(col)
                return cols
            if hasattr(f, "type"):  # InstrumentedAttribute
                return [f]  # type: ignore[return-value]
            # str
            col = getattr(self.model, f, None)
            if col is None:
                raise ValueError(f"Column '{f}' not found on {self.model.__name__}")
            return [col]

        def _ensure_text_columns(cols: Iterable[InstrumentedAttribute]) -> None:
            for c in cols:
                if not isinstance(getattr(c, "type", None), (String, Text)):
                    raise ValueError(f"Column '{c.key}' is not a text column")

        def _escape_like(val: str) -> str:
            # экранируем спецсимволы шаблона
            return val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

        cols = _as_columns(field)
        _ensure_text_columns(cols)

        q = _escape_like(query)
        if mode == "exact":
            pattern = q
        elif mode == "prefix":
            pattern = f"{q}%"
        elif mode == "suffix":
            pattern = f"%{q}"
        else:  # contains
            pattern = f"%{q}%"

        # строим OR по нескольким полям
        conds = []
        for c in cols:
            if case_insensitive and hasattr(c, "ilike"):
                conds.append(c.ilike(pattern, escape="\\"))
            else:
                conds.append(c.like(pattern, escape="\\"))

        stmt = select(self.model).where(or_(*conds))
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        stmt = stmt.offset(offset).limit(limit)

        res = await db.execute(stmt)
        return res.scalars().all()
    
    @property
    def _pk_names(self) -> set[str]:
        """Имена PK-колонок модели."""
        return {col.key for col in inspect(self.model).primary_key}

    async def update_fields(
        self,
        db: AsyncSession,
        db_obj: ModelType,
        fields: Mapping[str, Any],
    ) -> ModelType:
        """
        Частичное обновление (PATCH): меняем только переданные поля.
        PK и защищённые поля игнорируются.
        """
        if not fields:
            return db_obj

        for k, v in fields.items():
            if k in self._pk_names or k in self.protected_update_fields:
                continue
            setattr(db_obj, k, v)

        await db.flush()
        await db.refresh(db_obj)
        return db_obj
    
    async def paginate(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        filters: Optional[Iterable[ColumnElement[bool]]] = None,
        order_by: Optional[Sequence[ColumnElement]] = None,
    ) -> Tuple[List[ModelType], int]:
        """
        Получить страницу данных и общее количество записей с теми же фильтрами.

        Args:
            limit: Максимум записей на странице.
            offset: Смещение.
            filters: Итерабель фильтров where(...).
            order_by: Поля сортировки.

        Returns:
            (items, total):
                items — элементы текущей страницы,
                total — полное количество записей без учёта limit/offset.
        """
        filters = list(filters or [])

        # Основной запрос за данными
        stmt: Select = select(self.model)
        if filters:
            for f in filters:
                stmt = stmt.where(f)
        if order_by:
            stmt = stmt.order_by(*order_by)
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset:
            stmt = stmt.offset(offset)

        result = await self.session.execute(stmt)
        items: List[ModelType] = result.scalars().all()

        # Отдельный COUNT(*) c теми же фильтрами
        count_stmt: Select = select(func.count())
        base_count = select(self.model)
        if filters:
            for f in filters:
                base_count = base_count.where(f)
        # COUNT(*) от подзапроса (корректнее при сложных order_by/joins)
        count_stmt = select(func.count()).select_from(base_count.subquery())

        total: int = int(await self.session.scalar(count_stmt) or 0)
        return items, total