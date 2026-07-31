"""tsk-432: защита от того, чтобы школа осталась без администратора.

Роль `admin` — единственная, которой выдаются права на выдачу прав. Снять её
у последнего администратора значит запереть школу: вернуть роль будет некому,
кроме правки прямо в базе. Та же логика, что у блокировки входа
(`user_block_service`): проверяем ДО правки, а не после.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.association_tables import t_user_roles
from app.models.roles import Roles
from app.models.users import Users
from app.utils.exceptions import DomainError


async def assert_can_remove_role(
    db: AsyncSession, *, user_id: int, role_id: int, actor_id: int | None
) -> None:
    """Отказать, если снятие роли обезоружит школу или самого администратора."""
    role_name = (await db.execute(
        select(Roles.name).where(Roles.id == role_id)
    )).scalar_one_or_none()
    if role_name != "admin":
        return  # прочие роли снимаются свободно

    if actor_id is not None and actor_id == user_id:
        raise DomainError(
            "Нельзя снять роль администратора с самого себя — вы потеряете "
            "доступ к кабинету",
            status_code=409,
        )

    others = (await db.execute(
        select(func.count(func.distinct(Users.id)))
        .select_from(Users)
        .join(t_user_roles, t_user_roles.c.user_id == Users.id)
        .join(Roles, Roles.id == t_user_roles.c.role_id)
        .where(
            Roles.name == "admin",
            Users.id != user_id,
            Users.is_active.is_(True),
            Users.blocked_at.is_(None),
        )
    )).scalar() or 0
    if others == 0:
        raise DomainError(
            "Это последний администратор со входом — снять роль будет некому "
            "вернуть. Сначала назначьте другого",
            status_code=409,
        )
