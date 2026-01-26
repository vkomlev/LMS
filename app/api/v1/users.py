# app/api/v1/users.py
from typing import List, Optional
from enum import Enum
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import asc, desc
import logging

from app.core.logger import setup_logging
from app.api.deps import get_db
from app.api.v1.crud import create_crud_router
from app.services.users_service import UsersService
from app.schemas.users import UserID, UserRead, UserCreate, UserUpdate
from app.models.users import Users
from app.utils.pagination import Page, build_page

router = APIRouter(prefix="/users", tags=["users"])
service = UsersService()

setup_logging()
logger = logging.getLogger(__name__)


class SortByField(str, Enum):
    """Поля для сортировки"""
    full_name = "full_name"
    email = "email"
    created_at = "created_at"


class SortOrder(str, Enum):
    """Направление сортировки"""
    asc = "asc"
    desc = "desc"

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
    "/",
    response_model=Page[UserRead],  # type: ignore[name-defined]
    summary="Список пользователей с пагинацией, сортировкой и фильтрацией",
)
async def list_users(
    skip: int = Query(0, ge=0, description="Смещение для пагинации"),
    limit: int = Query(100, ge=1, le=1000, description="Максимум результатов на странице"),
    sort_by: Optional[SortByField] = Query(
        SortByField.full_name,
        description="Поле для сортировки"
    ),
    order: SortOrder = Query(
        SortOrder.asc,
        description="Направление сортировки (asc/desc)"
    ),
    role: Optional[str] = Query(
        None,
        description="Фильтр по роли (например, 'student' для получения только студентов)"
    ),
    db: AsyncSession = Depends(get_db),
) -> Page[UserRead]:
    """
    Получить список пользователей с пагинацией, сортировкой и фильтрацией по роли.
    
    Параметры:
    - skip: Смещение для пагинации
    - limit: Максимум результатов на странице
    - sort_by: Поле для сортировки (full_name, email, created_at)
    - order: Направление сортировки (asc, desc)
    - role: Фильтр по роли по имени (например, 'student')
    
    Примеры:
    - GET /api/v1/users/?skip=0&limit=50&sort_by=full_name&order=asc&role=student
    - GET /api/v1/users/?sort_by=created_at&order=desc
    """
    logger.info(
        "users.list skip=%s limit=%s sort_by=%s order=%s role=%s",
        skip, limit, sort_by, order, role
    )
    
    # Определяем поле для сортировки
    sort_field = getattr(Users, sort_by.value)
    order_func = asc if order == SortOrder.asc else desc
    order_by = [order_func(sort_field)]
    
    # Получаем данные через сервис
    items, total = await service.list_with_role_filter(
        db,
        role_name=role,
        limit=limit,
        offset=skip,
        order_by=order_by,
    )
    
    logger.debug("users.list -> %d items (total=%d)", len(items), total)
    
    return build_page(items, total=total, limit=limit, offset=skip)


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