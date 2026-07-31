# app/api/v1/user_roles.py
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.current_user import CurrentUser
from app.api.deps import require_role, get_async_db, get_db
from app.schemas.roles import RoleRead
from sqlalchemy import select

from app.models.roles import Roles
from app.services.user_roles_service import UserRolesService
from app.services import user_role_guard

router = APIRouter(prefix="/users/{user_id}/roles", tags=["user_roles"])
service = UserRolesService()


# tsk-433 Волна 3: чтение связей людей открыто кабинету методиста.
# Персональные данные, поэтому только методист и админ; `is_service` в
# require_role пропускает ТГ-ботов, которые ходят с ключом в адресе.
_PEOPLE_READ_GATE = require_role("methodist", "admin")

# tsk-432: НАЗНАЧЕНИЕ ролей — распорядительное решение о правах, а не учебная
# работа. Оно шире всего, что делает методист: ролью выдаётся в том числе
# доступ администратора. Поэтому запись уже чтения — только админ (и сервисный
# ключ, который ботам нужен для их собственных сценариев).
#
# До этой задачи обе записи висели на legacy `?api_key=` и из браузера были
# недоступны вовсе — тот же разрыв, что закрывали в Волнах 2-3 у контента.
_ROLE_WRITE_GATE = require_role("admin")

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
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_ROLE_WRITE_GATE),
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
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_ROLE_WRITE_GATE),
) -> None:
    """
    Снять роль role_id с пользователя user_id.

    Роль администратора снять у последнего администратора нельзя: школа
    осталась бы без человека, способного вернуть права, — та же защита, что у
    блокировки (tsk-432). Себя обезоруживать тоже не даём.
    """
    await user_role_guard.assert_can_remove_role(
        db,
        user_id=user_id,
        role_id=role_id,
        actor_id=None if current_user.is_service else current_user.id,
    )
    try:
        await service.remove_role(db, user_id, role_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))

# Справочник ролей нужен экрану, чтобы было из чего выбирать. Общий CRUD
# `/roles/` висит на legacy `?api_key=` и из браузера недоступен; трогать его
# нельзя — им ходят боты со своим контрактом. Поэтому отдельный узкий адрес.
catalog_router = APIRouter(prefix="/roles", tags=["roles"])


@catalog_router.get(
    "/catalog",
    response_model=List[RoleRead],
    summary="Справочник ролей",
    description="Все роли школы — для выбора в кабинете администратора.",
)
async def list_roles_catalog(
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_PEOPLE_READ_GATE),
) -> List[RoleRead]:
    rows = (await db.execute(select(Roles).order_by(Roles.id))).scalars().all()
    return [RoleRead.model_validate(r) for r in rows]
