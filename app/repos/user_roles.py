# app/repos/user_roles.py
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession


from app.models.association_tables import t_user_roles
from app.models.users import Users
from app.models.roles import Roles


class UserRolesRepository:
    """
    Репозиторий для операций many-to-many между Users и Roles через association table.
    """

    async def list_roles(self, db: AsyncSession, user_id: int) -> list[Roles]:
        """
        Вернуть список Role для пользователя user_id.
        """
        stmt = (
            select(Roles)
            .join(t_user_roles, Roles.id == t_user_roles.c.role_id)
            .where(t_user_roles.c.user_id == user_id)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    async def add_role(self, db: AsyncSession, user_id: int, role_id: int) -> None:
        """
        Назначить роль role_id пользователю user_id.
        Если уже есть — пропустить (ON CONFLICT DO NOTHING).
        """
        # Убедимся, что такие User и Role существуют
        user = await db.get(Users, user_id)
        role = await db.get(Roles, role_id)
        if not user or not role:
            raise ValueError("User or Role not found")

        stmt = (
            insert(t_user_roles)
            .values(user_id=user_id, role_id=role_id)
            .on_conflict_do_nothing(index_elements=["user_id", "role_id"])
        )
        await db.execute(stmt)
        await db.commit()

    async def remove_role(self, db: AsyncSession, user_id: int, role_id: int) -> None:
        """
        Удалить роль role_id у пользователя user_id.
        """
        stmt = (
            delete(t_user_roles)
            .where(
                t_user_roles.c.user_id == user_id,
                t_user_roles.c.role_id == role_id,
            )
        )
        await db.execute(stmt)
        await db.commit()
