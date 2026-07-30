# app/api/v1/user_roles.py
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.current_user import CurrentUser
from app.api.deps import require_role, get_async_db, get_db
from app.schemas.roles import RoleRead
from app.services.user_roles_service import UserRolesService

router = APIRouter(prefix="/users/{user_id}/roles", tags=["user_roles"])
service = UserRolesService()


# tsk-433 Волна 3: чтение связей людей открыто кабинету методиста.
# Персональные данные, поэтому только методист и админ; `is_service` в
# require_role пропускает ТГ-ботов, которые ходят с ключом в адресе.
_PEOPLE_READ_GATE = require_role("methodist", "admin")

@router.get("/", response_model=List[RoleRead])
async def list_user_roles(
    user_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_PEOPLE_READ_GATE),
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
