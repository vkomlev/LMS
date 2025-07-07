# app/api/v1/crud.py

import logging
from typing import Any, Dict, List, Type
from fastapi import APIRouter, Depends, HTTPException, Response, status, Body
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
import json

from app.api.deps import get_db
from app.services.base import BaseService

logger = logging.getLogger("api.crud")

def create_crud_router(
    *,
    prefix: str,
    tags: List[str],
    service: BaseService,
    create_schema: Type[BaseModel],
    read_schema: Type[BaseModel],
    update_schema: Type[BaseModel],
    pk_type: type = int,
) -> APIRouter:
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
            result = await service.create(db, obj_in.dict())
            logger.info(f"[{prefix}] created id=%s", getattr(result, "id", None))
            return result
        except Exception as e:
            logger.exception(f"[{prefix}] create failed")
            raise

    @router.get("/", response_model=List[read_schema])
    async def list_items(
        skip: int = 0,
        limit: int = 100,
        db: AsyncSession = Depends(get_db),
    ) -> List[Any]:
        logger.info(f"[{prefix}] list skip={skip} limit={limit}")
        try:
            items = await service.list(db, skip, limit)
            logger.debug(f"[{prefix}] list returned {len(items)} items")
            return items
        except Exception as e:
            logger.error(f"[{prefix}] list failed: {e}", exc_info=True)
            raise

    @router.get("/{item_id}", response_model=read_schema)
    async def get_item(
        item_id: Any,
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
            updated = await service.update(db, db_obj, obj_in.dict(exclude_unset=True))
            logger.info(f"[{prefix}] update id=%s success", item_id)
            return updated
        except Exception as e:
            logger.error(f"[{prefix}] update id={item_id} failed: {e}", exc_info=True)
            raise

    @router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_item(
        item_id: Any,
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

    return router
