from typing import Optional, List, Tuple, Sequence
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.models.users import Users
from app.repos.base import BaseRepository

class UsersRepository(BaseRepository[Users]):
    """
    Репозиторий для пользователей.
    Помимо CRUD из BaseRepository, добавляем get_by_tg_id.
    """
    def __init__(self) -> None:
        super().__init__(Users)

    async def list_with_role_filter(
        self,
        db: AsyncSession,
        *,
        role_name: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        order_by: Optional[Sequence[ColumnElement]] = None,
    ) -> Tuple[List[Users], int]:
        """
        Получить список пользователей с фильтрацией по роли.
        
        Args:
            role_name: Имя роли для фильтрации (например, "student")
            limit: Максимум записей
            offset: Смещение
            order_by: Поля сортировки
            
        Returns:
            (items, total): список пользователей и общее количество
        """
        from app.models.association_tables import t_user_roles
        from app.models.roles import Roles
        
        # Базовый запрос
        stmt = select(Users)
        
        # Фильтр по роли через JOIN
        if role_name:
            stmt = (
                stmt
                .join(t_user_roles, Users.id == t_user_roles.c.user_id)
                .join(Roles, t_user_roles.c.role_id == Roles.id)
                .where(Roles.name == role_name)
            )
        
        # Сортировка
        if order_by:
            # Добавляем NULLS LAST для всех полей сортировки, чтобы NULL значения шли в конец
            from sqlalchemy import nullslast
            order_by_with_nulls = []
            for order_expr in order_by:
                # nullslast работает с любыми выражениями сортировки (asc/desc)
                order_by_with_nulls.append(nullslast(order_expr))
            stmt = stmt.order_by(*order_by_with_nulls)
        
        # Пагинация
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset:
            stmt = stmt.offset(offset)
        
        # Выполняем запрос
        result = await db.execute(stmt)
        items: List[Users] = list(result.scalars().all())
        
        # Подсчет общего количества с теми же фильтрами
        count_stmt = select(func.count(Users.id))
        if role_name:
            count_stmt = (
                count_stmt
                .join(t_user_roles, Users.id == t_user_roles.c.user_id)
                .join(Roles, t_user_roles.c.role_id == Roles.id)
                .where(Roles.name == role_name)
            )
        
        total: int = int(await db.scalar(count_stmt) or 0)
        
        return items, total

