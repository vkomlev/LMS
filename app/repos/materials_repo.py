# app/repos/materials_repo.py
"""
Репозиторий для учебных материалов.

⚠️ Бизнес-логика order_position реализована в БД через триггеры:
- trg_set_material_order_position (автоматическая нумерация)
- trg_reorder_materials_after_delete (пересчёт после удаления)
См. docs/database-triggers-contract.md
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from app.models.materials import Materials
from app.repos.base import BaseRepository


class MaterialsRepository(BaseRepository[Materials]):
    def __init__(self) -> None:
        super().__init__(Materials)

    async def list_by_course(
        self,
        db: AsyncSession,
        course_id: int,
        *,
        q: Optional[str] = None,
        is_active: Optional[bool] = None,
        type_filter: Optional[str] = None,
        order_by: str = "order_position",
        skip: int = 0,
        limit: int = 100,
    ) -> Tuple[List[Materials], int]:
        """
        Список материалов курса с фильтрацией и пагинацией.
        q: поиск по title и external_uid (ILIKE %q%).

        Returns:
            (список материалов, общее количество без limit)
        """
        stmt = select(Materials).where(Materials.course_id == course_id)
        count_stmt = select(func.count()).select_from(Materials).where(Materials.course_id == course_id)

        if q and q.strip():
            pattern = f"%{q.strip()}%"
            cond = (Materials.title.ilike(pattern)) | (Materials.external_uid.ilike(pattern))
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)
        if is_active is not None:
            stmt = stmt.where(Materials.is_active == is_active)
            count_stmt = count_stmt.where(Materials.is_active == is_active)
        if type_filter is not None:
            stmt = stmt.where(Materials.type == type_filter)
            count_stmt = count_stmt.where(Materials.type == type_filter)

        total = (await db.execute(count_stmt)).scalar() or 0

        if order_by == "order_position":
            stmt = stmt.order_by(Materials.order_position.asc().nulls_last(), Materials.id.asc())
        elif order_by == "title":
            stmt = stmt.order_by(Materials.title.asc(), Materials.id.asc())
        elif order_by == "created_at":
            stmt = stmt.order_by(Materials.created_at.desc(), Materials.id.asc())
        else:
            stmt = stmt.order_by(Materials.order_position.asc().nulls_last(), Materials.id.asc())

        stmt = stmt.offset(skip).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all()), total

    async def search_materials(
        self,
        db: AsyncSession,
        q: str,
        *,
        course_id: Optional[int] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Tuple[List[Materials], int]:
        """
        Поиск материалов по title и external_uid (ILIKE).
        course_id: опционально; при отсутствии — поиск по всем курсам.

        Returns:
            (список материалов, общее количество без limit)
        """
        if not q or not q.strip():
            return [], 0
        pattern = f"%{q.strip()}%"
        cond = (Materials.title.ilike(pattern)) | (Materials.external_uid.ilike(pattern))

        stmt = select(Materials).where(cond)
        count_stmt = select(func.count()).select_from(Materials).where(cond)
        if course_id is not None:
            stmt = stmt.where(Materials.course_id == course_id)
            count_stmt = count_stmt.where(Materials.course_id == course_id)

        total = (await db.execute(count_stmt)).scalar() or 0
        stmt = stmt.order_by(Materials.course_id.asc(), Materials.order_position.asc().nulls_last(), Materials.id.asc())
        stmt = stmt.offset(skip).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all()), total

    async def reorder_materials(
        self,
        db: AsyncSession,
        course_id: int,
        material_orders: List[Dict[str, int]],
    ) -> List[Materials]:
        """
        Массовое изменение порядка материалов курса.
        Отключает триггер нумерации на время обновления, затем обновляет order_position.
        """
        if not material_orders:
            return []

        await db.execute(text("SELECT set_config('app.skip_material_order_trigger', 'true', true)"))
        for item in material_orders:
            mid = item["material_id"]
            pos = item["order_position"]
            await db.execute(
                update(Materials)
                .where(Materials.id == mid, Materials.course_id == course_id)
                .values(order_position=pos)
            )
        await db.commit()

        material_ids = [item["material_id"] for item in material_orders]
        stmt = select(Materials).where(
            Materials.course_id == course_id,
            Materials.id.in_(material_ids),
        ).order_by(Materials.order_position.asc().nulls_last())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_max_order_position(self, db: AsyncSession, course_id: int) -> int:
        """Максимальная order_position в курсе (0 если материалов нет)."""
        stmt = select(func.coalesce(func.max(Materials.order_position), 0)).where(
            Materials.course_id == course_id
        )
        r = await db.execute(stmt)
        return (r.scalar() or 0) or 0

    async def move_material(
        self,
        db: AsyncSession,
        material_id: int,
        new_order_position: Optional[int],
        target_course_id: Optional[int] = None,
    ) -> Optional[Materials]:
        """
        Переместить материал в новую позицию (в том же курсе или в другой).
        new_order_position: если None и курс меняется — ставим в конец (max+1).
        Триггер пересчитает order_position в целевом курсе.
        """
        material = await self.get(db, material_id)
        if not material:
            return None

        target_course = target_course_id if target_course_id is not None else material.course_id
        pos = new_order_position
        if pos is None and target_course != material.course_id:
            pos = await self.get_max_order_position(db, target_course) + 1
        elif pos is None:
            pos = await self.get_max_order_position(db, target_course) + 1

        updates: Dict[str, Any] = {"order_position": pos}
        if target_course_id is not None and target_course_id != material.course_id:
            updates["course_id"] = target_course_id

        await db.execute(
            update(Materials).where(Materials.id == material_id).values(**updates)
        )
        await db.commit()
        await db.refresh(material)
        return material

    async def bulk_update_active(
        self,
        db: AsyncSession,
        course_id: int,
        material_ids: List[int],
        is_active: bool,
    ) -> int:
        """Массовое обновление is_active для материалов курса. Возвращает количество обновлённых."""
        if not material_ids:
            return 0
        result = await db.execute(
            update(Materials)
            .where(
                Materials.course_id == course_id,
                Materials.id.in_(material_ids),
            )
            .values(is_active=is_active)
        )
        await db.commit()
        return result.rowcount or 0

    async def copy_material(
        self,
        db: AsyncSession,
        material_id: int,
        target_course_id: int,
        order_position: Optional[int] = None,
    ) -> Optional[Materials]:
        """
        Копировать материал в другой курс (без id, created_at, updated_at).
        order_position=None — триггер поставит в конец.
        """
        source = await self.get(db, material_id)
        if not source:
            return None

        data = {
            "course_id": target_course_id,
            "title": source.title,
            "description": source.description,
            "caption": source.caption,
            "type": source.type,
            "content": dict(source.content),
            "order_position": order_position,
            "is_active": source.is_active,
            "external_uid": None,
        }
        return await self.create(db, data)

    async def get_stats_by_course(
        self,
        db: AsyncSession,
        course_id: int,
    ) -> Dict[str, Any]:
        """Статистика материалов курса: total, by_type, active, inactive."""
        base = select(Materials).where(Materials.course_id == course_id)

        total = (await db.execute(select(func.count()).select_from(Materials).where(Materials.course_id == course_id))).scalar() or 0
        active = (await db.execute(select(func.count()).select_from(Materials).where(Materials.course_id == course_id, Materials.is_active.is_(True)))).scalar() or 0
        inactive = total - active

        by_type_stmt = (
            select(Materials.type, func.count(Materials.id))
            .where(Materials.course_id == course_id)
            .group_by(Materials.type)
        )
        type_rows = (await db.execute(by_type_stmt)).all()
        by_type = {row[0]: row[1] for row in type_rows}

        return {
            "total": total,
            "by_type": by_type,
            "active": active,
            "inactive": inactive,
        }
