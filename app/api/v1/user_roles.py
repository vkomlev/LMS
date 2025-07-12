# app/api/v1/user_roles.py
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.roles import RoleRead
from app.services.user_roles_service import UserRolesService

router = APIRouter(prefix="/users/{user_id}/roles", tags=["user_roles"])
service = UserRolesService()


@router.get("/", response_model=List[RoleRead])
async def list_user_roles(
    user_id: int,
    db: AsyncSession = Depends(get_db),
) -> List[RoleRead]:
    """
    Список ролей, назначенных пользователю.
    """
    try:
        return await service.list_roles(db, user_id)
    except Exception as e:
        # если пользователь не существует, можно проверить и выдать 404
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))


@router.post(
    "/{role_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def add_user_role(
    user_id: int,
    role_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Назначить роль role_id пользователю user_id.
    """
    try:
        await service.add_role(db, user_id, role_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))


@router.delete(
    "/{role_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_user_role(
    user_id: int,
    role_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Снять роль role_id с пользователя user_id.
    """
    try:
        await service.remove_role(db, user_id, role_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
