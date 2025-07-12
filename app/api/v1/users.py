# app/api/v1/users.py

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.services.users_service import UsersService
from app.schemas.users import UserID

router = APIRouter(prefix="/users", tags=["users"])
service = UsersService()

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
