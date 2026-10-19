# app/api/v1/crud.py

import logging
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Type, TypeVar
from fastapi import APIRouter, Depends, HTTPException, Response, status, Body
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
import json

from app.api.deps import get_db
from app.services.base import BaseService
from app.utils.pagination import Page, build_page

logger = logging.getLogger("api.crud")

ModelT = TypeVar("ModelT")
SchemaReadT = TypeVar("SchemaReadT")
SchemaCreateT = TypeVar("SchemaCreateT")
SchemaUpdateT = TypeVar("SchemaUpdateT")


def mark_manual_edit(
    db_obj: Any, update_data: Dict[str, Any], fields: Optional[FrozenSet[str]], reason: str
) -> bool:
    """Поставить пометку ручной правки (`content_provenance`), если правилось содержимое.

    Нужна, чтобы переиздание из ContentBackbone не затёрло правку и чек
    cb-drift не показывал её как «правку без пометки» (tsk-1385, tsk-1388).
    Пометка ставится на пару полей целиком, как в кабинете методиста
    (`PATCH /tasks/{id}`). Если клиент сам прислал `content_provenance`,
    его значение не перебивается.

    :returns: True, если пометка поставлена.
    """
    if not fields or "content_provenance" in update_data:
        return False
    if not fields & set(update_data):
        return False
    db_obj.content_provenance = {
        "source": "manual_web",
        "edited_at": datetime.now(timezone.utc).isoformat(),
        # общий роутер работает по api_key, пользователя он не знает
        "edited_by": "api_key",
        "fields": sorted(fields),
        "reason": reason,
    }
    return True

def create_composite_router(
    *,
    prefix: str,
    tags: List[str],
    service: BaseService,
    create_schema: Type[BaseModel],
    read_schema: Type[BaseModel],
    update_schema: Type[BaseModel],
    pk_fields: List[str],
) -> APIRouter:
    """
    Генерирует CRUD-роутер для таблиц с составным PK.
    pk_fields — список имён полей PK в порядке URL-параметров.
    """
    router = APIRouter(prefix=prefix, tags=tags)

    # POST / → create
    @router.post("/", response_model=read_schema, status_code=status.HTTP_201_CREATED)
    async def create_item(
        *,
        obj_in: create_schema = Body(...),
        db: AsyncSession = Depends(get_db),
    ):
        return await service.create(db, obj_in.model_dump())

    # GET /{key1}/{key2}/… → read
    path = "/" + "/".join(f"{{{f}}}" for f in pk_fields)
    @router.get(path, response_model=read_schema)
    async def get_item(
        db: AsyncSession = Depends(get_db),
        **kwargs: int,        
    ):
        """
        kwargs looks like {'user_id': 1, 'achievement_id': 2}
        """
        obj = await service.get_by_keys(db, kwargs)
        if not obj:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        return obj

    # PUT /{key1}/{key2}/… → update
    @router.put(path, response_model=read_schema)
    async def update_item(
        *,
        obj_in: update_schema = Body(...),
        db: AsyncSession = Depends(get_db),
        **kwargs: int,
    ):
        updated = await service.update_by_keys(db, kwargs, obj_in.model_dump(exclude_unset=True))
        if not updated:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        return updated

    # DELETE /{key1}/{key2}/… → delete
    @router.delete(path, status_code=status.HTTP_204_NO_CONTENT)
    async def delete_item(
        db: AsyncSession = Depends(get_db),
        **kwargs: int,        
    ):
        deleted = await service.delete_by_keys(db, kwargs)
        if not deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
    
def create_crud_router(
    *,
    prefix: str,
    tags: List[str],
    service: BaseService,
    create_schema: Type[BaseModel],
    read_schema: Type[BaseModel],
    update_schema: Type[BaseModel],
    pk_type: type = int,
    include_delete: bool = True,
    manual_edit_fields: Optional[FrozenSet[str]] = None,
) -> APIRouter:
    """
    Генерирует CRUD-роутер.

    :param include_delete: регистрировать ли generic DELETE /{item_id}.
        Отключается, когда ресурс предоставляет собственный обработчик удаления
        (например, courses — см. courses_extra.delete_course_endpoint, tsk-121).
    :param manual_edit_fields: поля содержимого; их правка через `PUT /{item_id}`
        ставит пометку ручной правки (`content_provenance`, tsk-1388).
    """
    router = APIRouter(prefix=prefix, tags=tags)

    @router.post(
        "/", response_model=read_schema, status_code=status.HTTP_201_CREATED
    )
    async def create_item(
        *,
        obj_in: create_schema  = Body(..., description="Данные для создания"),
        db: AsyncSession = Depends(get_db),
    ) -> Any:
        payload = obj_in.json() if hasattr(obj_in, "json") else json.dumps(obj_in)
        logger.info(f"[{prefix}] create payload: %s", payload)
        try:
            result = await service.create(db, obj_in.model_dump())
            logger.info(f"[{prefix}] created id=%s", getattr(result, "id", None))
            return result
        except Exception as e:
            logger.exception(f"[{prefix}] create failed")
            raise

    # --- ИЗМЕНЕНО: список теперь возвращает Page[read_schema] ---
    @router.get("/", response_model=Page[read_schema])  # type: ignore[name-defined]
    async def list_items(
        skip: int = 0,
        limit: int = 100,
        db: AsyncSession = Depends(get_db),
    ) -> Any:
        """
        Возвращает Page[T]: items + meta(total, limit, offset=skip).
        Параметры совместимы с текущими клиентами (skip/limit).
        """
        logger.info(f"[{prefix}] list skip={skip} limit={limit}")
        try:
            items, total = await service.paginate(db, limit=limit, offset=skip)
            logger.debug(f"[{prefix}] list returned {len(items)} items (total={total})")
            return build_page(items, total=total, limit=limit, offset=skip)
        except Exception as e:
            logger.error(f"[{prefix}] list failed: {e}", exc_info=True)
            raise

    @router.get("/{item_id}", response_model=read_schema)
    async def get_item(
        item_id: pk_type,
        db: AsyncSession = Depends(get_db),
    ) -> Any:
        logger.info(f"[{prefix}] get id={item_id}")
        obj = await service.get_by_id(db, item_id)
        if not obj:
            logger.warning(f"[{prefix}] get id={item_id} not found")
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        logger.debug(f"[{prefix}] get id={item_id} success")
        return obj

    @router.put("/{item_id}", response_model=read_schema)
    async def update_item(
        *,
        item_id: pk_type,
        obj_in: update_schema  = Body(..., description="Данные для создания"),
        db: AsyncSession = Depends(get_db),
    ) -> Any:
        logger.info(f"[{prefix}] update id=%s payload: %s", item_id, obj_in.json())
        db_obj = await service.get_by_id(db, item_id)
        if not db_obj:
            logger.warning(f"[{prefix}] update id={item_id} not found")
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        try:
            # Исключаем только не установленные поля
            # None значения для обязательных полей фильтруются в репозитории
            update_data = obj_in.model_dump(exclude_unset=True)
            updated = await service.update(db, db_obj, update_data)
            # tsk-1388: правка содержимого — правка человека. Решение tsk-808
            # (пометку ставит только кабинет методиста) отменено оператором 19.10.2026.
            if mark_manual_edit(updated, update_data, manual_edit_fields, "generic-put"):
                await db.commit()
                await db.refresh(updated)
                logger.info(f"[{prefix}] update id=%s помечено как ручная правка", item_id)
            logger.info(f"[{prefix}] update id=%s success", item_id)
            return updated
        except Exception as e:
            logger.error(f"[{prefix}] update id={item_id} failed: {e}", exc_info=True)
            raise

    if include_delete:
        @router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
        async def delete_item(
            item_id: pk_type,
            db: AsyncSession = Depends(get_db),
        ) -> Response:
            logger.info(f"[{prefix}] delete id={item_id}")
            db_obj = await service.get_by_id(db, item_id)
            if not db_obj:
                logger.warning(f"[{prefix}] delete id={item_id} not found")
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
            try:
                await service.delete(db, db_obj)
                logger.info(f"[{prefix}] delete id={item_id} success")
                return Response(status_code=status.HTTP_204_NO_CONTENT)
            except Exception as e:
                logger.error(f"[{prefix}] delete id={item_id} failed: {e}", exc_info=True)
                raise


    @router.patch("/{item_id}", response_model=read_schema)
    async def patch_item(
        *,
        item_id: pk_type,
        obj_in: update_schema = Body(..., description="Частичное обновление"),
        db: AsyncSession = Depends(get_db),
    ) -> Any:
        logger.info(f"[{prefix}] patch id={item_id}")
        db_obj = await service.get_by_id(db, item_id)
        if not db_obj:
            logger.warning(f"[{prefix}] patch id={item_id} not found")
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

        # только переданные поля:
        payload = obj_in.model_dump(exclude_unset=True)
        try:
            # если сделали BaseService.patch — можно звать его
            updated = await service.update(db, db_obj, payload)
            logger.info(f"[{prefix}] patch id={item_id} success")
            return updated
        except Exception:
            logger.exception(f"[{prefix}] patch id={item_id} failed")
            raise
    
    return router
