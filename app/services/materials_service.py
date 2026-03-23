# app/services/materials_service.py
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.materials import Materials
from app.repos.courses_repo import CoursesRepository
from app.repos.materials_repo import MaterialsRepository
from app.schemas.materials import (
    MaterialsBulkUpsertItem,
    MaterialsBulkUpsertResponse,
    MaterialsBulkUpsertResultItem,
)
from app.services.base import BaseService
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

# Ключ идемпотентности или «осиротевший» элемент (без парсабельного course_id+external_uid)
BulkKey = Union[Tuple[int, str], Tuple[Literal["__orphan__"], int]]


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

    def _material_unchanged(self, existing: Materials, item: MaterialsBulkUpsertItem) -> bool:
        """Сравнение снимка полей (для статуса unchanged без лишнего UPDATE)."""
        if existing.title != item.title:
            return False
        if (existing.description or "") != (item.description or ""):
            return False
        if (existing.caption or "") != (item.caption or ""):
            return False
        if existing.type != item.type:
            return False
        if bool(existing.is_active) != bool(item.is_active):
            return False
        if item.order_position is not None and existing.order_position != item.order_position:
            return False
        try:
            left = json.dumps(existing.content, sort_keys=True, default=str)
            right = json.dumps(item.content, sort_keys=True, default=str)
        except TypeError:
            return False
        return left == right

    @staticmethod
    def _parse_bulk_idempotency_key(raw: dict) -> Tuple[int, str] | None:
        """Пара (course_id, external_uid) для дедупликации; None если ключа нет."""
        try:
            c = raw.get("course_id")
            if c is None:
                return None
            cid = int(c)
            e = raw.get("external_uid")
            if not isinstance(e, str):
                return None
            e = e.strip()
            if not e:
                return None
            return (cid, e)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _format_validation_error(exc: ValidationError) -> str:
        parts: List[str] = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", ()))
            parts.append(f"{loc}: {err.get('msg', '')}")
        return "; ".join(parts)[:4000]

    @staticmethod
    def _result_ids_for_row(raw: dict, key: BulkKey) -> Tuple[int, str]:
        if key[0] == "__orphan__":
            c = raw.get("course_id", 0)
            try:
                ci = int(c) if c is not None else 0
            except (TypeError, ValueError):
                ci = 0
            e = raw.get("external_uid", "")
            if not isinstance(e, str):
                e = str(e) if e is not None else ""
            return ci, e
        return int(key[0]), str(key[1])

    async def bulk_upsert(
        self,
        db: AsyncSession,
        items: List[Dict[str, Any]],
    ) -> MaterialsBulkUpsertResponse:
        """
        Идемпотентный пакетный upsert по (course_id, external_uid).

        - Дубликаты ключей в batch: последний по порядку входа выигрывает.
        - Ошибки валидации одного элемента не рвут весь запрос 422 — per-item error.
        - Фаза записи в БД: одна транзакция (flush + один commit); при сбое — rollback всего batch-записей.
        """
        order_keys: List[BulkKey] = []
        payload_by_key: Dict[BulkKey, dict] = {}
        orphan_i = 0

        for raw in items:
            if not isinstance(raw, dict):
                k: BulkKey = ("__orphan__", orphan_i)
                orphan_i += 1
                order_keys.append(k)
                payload_by_key[k] = {"__invalid_shape__": True, "__raw_type__": type(raw).__name__}
                continue
            pk = self._parse_bulk_idempotency_key(raw)
            if pk is None:
                k = ("__orphan__", orphan_i)
                orphan_i += 1
                order_keys.append(k)
                payload_by_key[k] = raw
            else:
                if pk not in payload_by_key:
                    order_keys.append(pk)
                payload_by_key[pk] = raw

        int_course_ids = {int(k[0]) for k in order_keys if isinstance(k[0], int)}
        existing_course_ids = await self._courses_repo.filter_existing_ids(db, int_course_ids)

        val_results: Dict[BulkKey, MaterialsBulkUpsertResultItem] = {}
        pending: List[Tuple[BulkKey, MaterialsBulkUpsertItem]] = []

        for key in order_keys:
            raw = payload_by_key[key]
            cid_out, ext_out = self._result_ids_for_row(raw, key)

            if raw.get("__invalid_shape__"):
                val_results[key] = MaterialsBulkUpsertResultItem(
                    course_id=cid_out,
                    external_uid=ext_out,
                    status="error",
                    error=f"Элемент batch должен быть JSON-объектом, получен {raw.get('__raw_type__', '?')}",
                    error_type="validation",
                )
                continue

            try:
                item = MaterialsBulkUpsertItem.model_validate(raw)
            except ValidationError as e:
                val_results[key] = MaterialsBulkUpsertResultItem(
                    course_id=cid_out,
                    external_uid=ext_out,
                    status="error",
                    error=self._format_validation_error(e),
                    error_type="validation",
                )
                continue

            if item.course_id not in existing_course_ids:
                val_results[key] = MaterialsBulkUpsertResultItem(
                    course_id=item.course_id,
                    external_uid=item.external_uid,
                    status="error",
                    error=f"Курс с ID {item.course_id} не найден",
                    error_type="validation",
                )
                continue

            pending.append((key, item))

        db_by_key: Dict[BulkKey, MaterialsBulkUpsertResultItem] = {}
        batch_errors: List[str] = []

        if pending:
            try:
                pairs = [(it.course_id, it.external_uid) for _, it in pending]
                existing_map = await self.repo.find_by_course_external_pairs(db, pairs)
                for key, item in pending:
                    cid, ext = item.course_id, item.external_uid
                    payload_data: Dict[str, Any] = {
                        "course_id": item.course_id,
                        "title": item.title,
                        "type": item.type,
                        "content": item.content,
                        "description": item.description,
                        "caption": item.caption,
                        "order_position": item.order_position,
                        "is_active": item.is_active,
                        "external_uid": item.external_uid,
                    }
                    row_key = (cid, ext)
                    existing = existing_map.get(row_key)
                    if existing:
                        if self._material_unchanged(existing, item):
                            db_by_key[key] = MaterialsBulkUpsertResultItem(
                                course_id=cid,
                                external_uid=ext,
                                status="unchanged",
                                material_id=existing.id,
                            )
                        else:
                            await self.repo.update(db, existing, payload_data, commit=False)
                            await db.refresh(existing)
                            existing_map[row_key] = existing
                            db_by_key[key] = MaterialsBulkUpsertResultItem(
                                course_id=cid,
                                external_uid=ext,
                                status="updated",
                                material_id=existing.id,
                            )
                    else:
                        new_m = await self.repo.create(db, payload_data, commit=False)
                        existing_map[row_key] = new_m
                        db_by_key[key] = MaterialsBulkUpsertResultItem(
                            course_id=cid,
                            external_uid=ext,
                            status="created",
                            material_id=new_m.id,
                        )
                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.exception("bulk_upsert: откат транзакции записи")
                err_text = str(e)[:4000]
                batch_errors.append(f"Транзакция bulk-upsert откатена: {err_text}")
                for key, item in pending:
                    db_by_key[key] = MaterialsBulkUpsertResultItem(
                        course_id=item.course_id,
                        external_uid=item.external_uid,
                        status="error",
                        error=err_text,
                        error_type="runtime",
                    )

        final_items: List[MaterialsBulkUpsertResultItem] = []
        created_n = updated_n = unchanged_n = 0
        for key in order_keys:
            if key in val_results:
                final_items.append(val_results[key])
            elif key in db_by_key:
                r = db_by_key[key]
                final_items.append(r)
                if r.status == "created":
                    created_n += 1
                elif r.status == "updated":
                    updated_n += 1
                elif r.status == "unchanged":
                    unchanged_n += 1

        processed = created_n + updated_n + unchanged_n
        return MaterialsBulkUpsertResponse(
            processed=processed,
            created=created_n,
            updated=updated_n,
            unchanged=unchanged_n,
            items=final_items,
            errors=batch_errors,
        )
