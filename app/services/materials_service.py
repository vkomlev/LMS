# app/services/materials_service.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.materials import Materials
from app.repos.courses_repo import CoursesRepository
from app.repos.materials_repo import MaterialsRepository
from app.services.base import BaseService
from app.utils.exceptions import DomainError


class MaterialsService(BaseService[Materials]):
    """
    Сервис для учебных материалов.
    Валидирует курс/материалы перед операциями, обрабатывает ошибки триггеров БД.
    """

    def __init__(self, repo: MaterialsRepository | None = None):
        super().__init__(repo or MaterialsRepository())
        self._courses_repo = CoursesRepository()

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
        """Список материалов курса с фильтрацией и пагинацией. q — поиск по title/external_uid. Возвращает (items, total)."""
        course = await self._courses_repo.get(db, course_id)
        if not course:
            raise DomainError(f"Курс с ID {course_id} не найден", status_code=404)
        return await self.repo.list_by_course(
            db,
            course_id,
            q=q,
            is_active=is_active,
            type_filter=type_filter,
            order_by=order_by,
            skip=skip,
            limit=limit,
        )

    async def search_materials(
        self,
        db: AsyncSession,
        q: str,
        *,
        course_id: Optional[int] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Tuple[List[Materials], int]:
        """Поиск материалов по title и external_uid. course_id опционально — при отсутствии поиск по всем курсам. Возвращает (items, total)."""
        return await self.repo.search_materials(
            db, q, course_id=course_id, skip=skip, limit=limit
        )

    async def reorder_materials(
        self,
        db: AsyncSession,
        course_id: int,
        material_orders: List[Dict[str, int]],
    ) -> List[Materials]:
        """Массовое изменение порядка материалов курса. Проверяет, что все material_id принадлежат курсу."""
        if not material_orders:
            return []

        course = await self._courses_repo.get(db, course_id)
        if not course:
            raise DomainError(f"Курс с ID {course_id} не найден", status_code=404)

        material_ids = [item["material_id"] for item in material_orders]
        items, _ = await self.repo.list_by_course(db, course_id, limit=10000)
        ids_in_course = {m.id for m in items}
        for mid in material_ids:
            if mid not in ids_in_course:
                raise DomainError(
                    f"Материал с ID {mid} не принадлежит курсу {course_id} или не найден",
                    status_code=400,
                )

        try:
            return await self.repo.reorder_materials(db, course_id, material_orders)
        except IntegrityError as e:
            raise DomainError(
                f"Ошибка при изменении порядка материалов: {e!s}",
                status_code=400,
            )

    async def move_material(
        self,
        db: AsyncSession,
        material_id: int,
        new_order_position: Optional[int],
        target_course_id: Optional[int] = None,
    ) -> Materials:
        """Переместить материал. При смене курса new_order_position можно не передавать — материал встанет в конец. В рамках того же курса new_order_position обязателен."""
        material = await self.repo.get(db, material_id)
        if not material:
            raise DomainError(f"Материал с ID {material_id} не найден", status_code=404)

        same_course = target_course_id is None or target_course_id == material.course_id
        if same_course and new_order_position is None:
            raise DomainError(
                "При перемещении внутри курса укажите new_order_position",
                status_code=400,
            )

        if target_course_id is not None and target_course_id != material.course_id:
            course = await self._courses_repo.get(db, target_course_id)
            if not course:
                raise DomainError(f"Курс с ID {target_course_id} не найден", status_code=404)

        try:
            updated = await self.repo.move_material(
                db, material_id, new_order_position, target_course_id
            )
        except IntegrityError as e:
            raise DomainError(
                f"Ошибка при перемещении материала: {e!s}",
                status_code=400,
            )
        if not updated:
            raise DomainError(f"Материал с ID {material_id} не найден", status_code=404)
        return updated

    async def bulk_update_active(
        self,
        db: AsyncSession,
        course_id: int,
        material_ids: List[int],
        is_active: bool,
    ) -> int:
        """Массовое обновление is_active. Проверяет, что все material_id принадлежат курсу."""
        if not material_ids:
            return 0

        course = await self._courses_repo.get(db, course_id)
        if not course:
            raise DomainError(f"Курс с ID {course_id} не найден", status_code=404)

        items, _ = await self.repo.list_by_course(db, course_id, limit=10000)
        ids_in_course = {m.id for m in items}
        for mid in material_ids:
            if mid not in ids_in_course:
                raise DomainError(
                    f"Материал с ID {mid} не принадлежит курсу {course_id} или не найден",
                    status_code=400,
                )

        return await self.repo.bulk_update_active(db, course_id, material_ids, is_active)

    async def copy_material(
        self,
        db: AsyncSession,
        material_id: int,
        target_course_id: int,
        order_position: Optional[int] = None,
    ) -> Materials:
        """Копировать материал в другой курс. Проверяет существование материала и целевого курса."""
        material = await self.repo.get(db, material_id)
        if not material:
            raise DomainError(f"Материал с ID {material_id} не найден", status_code=404)

        course = await self._courses_repo.get(db, target_course_id)
        if not course:
            raise DomainError(f"Курс с ID {target_course_id} не найден", status_code=404)

        try:
            copied = await self.repo.copy_material(
                db, material_id, target_course_id, order_position
            )
        except IntegrityError as e:
            raise DomainError(
                f"Ошибка при копировании материала: {e!s}",
                status_code=400,
            )
        if not copied:
            raise DomainError(f"Материал с ID {material_id} не найден", status_code=404)
        return copied

    async def get_stats_by_course(
        self,
        db: AsyncSession,
        course_id: int,
    ) -> Dict[str, Any]:
        """Статистика материалов курса. Проверяет существование курса."""
        course = await self._courses_repo.get(db, course_id)
        if not course:
            raise DomainError(f"Курс с ID {course_id} не найден", status_code=404)
        return await self.repo.get_stats_by_course(db, course_id)
