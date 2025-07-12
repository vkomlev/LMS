# app/api/v1/access_requests.py
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.v1.crud import create_crud_router
from app.schemas.access_requests import (
    AccessRequestCreate,
    AccessRequestRead,
    AccessRequestUpdate,
    AccessRequestFlag,
)
from app.services.access_requests_service import AccessRequestsService

logger = logging.getLogger("api.access_requests")
service = AccessRequestsService()

router = APIRouter(prefix="/access_requests", tags=["access_requests"])

# 4.1 Подключаем стандартный CRUD (POST, GET/{id}, PUT/{id}, DELETE/{id})
router.include_router(
    create_crud_router(
        prefix="",
        tags=["access_requests"],
        service=service,
        create_schema=AccessRequestCreate,
        read_schema=AccessRequestRead,
        update_schema=AccessRequestUpdate,
        pk_type=int,
    ),
    prefix="",
)

# 4.2 Кастомный GET /access_requests/flag/{flag}
@router.get(
    "/flag/{flag}",
    response_model=List[AccessRequestRead],
    summary="Список запросов по статусу",
)
async def list_by_flag(
    flag: AccessRequestFlag,
    db: AsyncSession = Depends(get_db),
) -> List[AccessRequestRead]:
    """
    Вернуть все AccessRequests с заданным `flag`, отсортированные по времени запроса.
    """
    try:
        return await service.list_by_flag(db, flag.value)
    except Exception as e:
        logger.error("list_by_flag failed: %s", e, exc_info=True)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Не удалось получить список запросов по флагу",
        )
