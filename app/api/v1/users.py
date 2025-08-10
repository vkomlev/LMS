# app/api/v1/users.py
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from app.core.logger import setup_logging
from app.api.deps import get_db
from app.api.v1.crud import create_crud_router
from app.services.users_service import UsersService
from app.schemas.users import UserID, UserRead, UserCreate, UserUpdate
from app.models.users import Users

router = APIRouter(prefix="/users", tags=["users"])
service = UsersService()

setup_logging()
logger = logging.getLogger(__name__)

@router.get(
    "/search",
    response_model=List[UserRead],
    summary="Поиск пользователей по фрагменту имени (full_name)",
)
async def search_users_by_name(
    q: str = Query(..., min_length=2, description="Фрагмент имени"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> List[UserRead]:
    """
    Ищет по полю `full_name` (ILIKE %q%). Возвращает список пользователей.
    """
    logger.info("users.search q=%r limit=%s offset=%s", q, limit, offset)
    items = await service.search_text(
        db,
        field="full_name",
        query=q,
        mode="contains",
        case_insensitive=True,
        limit=limit,
        offset=offset,
        order_by=Users.full_name,  # опционально сортируем по имени
    )
    logger.debug("users.search -> %d rows", len(items))
    return items
@router.get(
    "/by-tg/{tg_id}",
    response_model=UserID,
    status_code=status.HTTP_200_OK,
    summary="Получить ID пользователя по Telegram ID",
)
async def get_user_id_by_tg(
    tg_id: int,
    db: AsyncSession = Depends(get_db),
) -> UserID:
    """
    Ищет пользователя по его tg_id и возвращает только поле `id`.
    """
    user = await service.get_id_by_tg_id(db, tg_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with tg_id={tg_id} not found",
        )
    return {"id": user}

crud_router = create_crud_router(
    prefix="",              # <--- ВАЖНО: пусто, т.к. сам router уже с prefix="/users"
    tags=["users"],
    service=service,        # ваш UsersService(), созданный выше
    create_schema=UserCreate,
    read_schema=UserRead,
    update_schema=UserUpdate,
    pk_type=int,
)
router.include_router(crud_router)