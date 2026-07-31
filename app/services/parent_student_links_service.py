# app/services/parent_student_links_service.py

from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.roles import Roles
from app.models.users import Users
from app.repos.parent_student_links_repository import ParentStudentLinksRepository
from app.repos.user_roles import UserRolesRepository

_PARENT_ROLE_NAME = "parent"


class ParentStudentLinksService:
    """
    Сервис для работы со связями родитель↔ученик (tsk-478, кабинет родителя).
    """

    def __init__(
        self,
        repo: ParentStudentLinksRepository | None = None,
        roles_repo: UserRolesRepository | None = None,
    ) -> None:
        self.repo = repo or ParentStudentLinksRepository()
        self.roles_repo = roles_repo or UserRolesRepository()

    async def list_children(self, db: AsyncSession, parent_id: int) -> List[Users]:
        """Вернуть всех учеников, привязанных к родителю."""
        return await self.repo.list_children(db, parent_id)

    async def list_parents(self, db: AsyncSession, student_id: int) -> List[Users]:
        """Вернуть всех родителей, привязанных к ученику."""
        return await self.repo.list_parents(db, student_id)

    async def is_linked(self, db: AsyncSession, parent_id: int, student_id: int) -> bool:
        """Есть ли связка — IDOR-гейт для дашборда ученика."""
        return await self.repo.is_linked(db, parent_id, student_id)

    async def add_link(self, db: AsyncSession, parent_id: int, student_id: int) -> None:
        """
        Создать связь родитель↔ученик и идемпотентно назначить роль `parent`.

        Гочта (см. docs/specs/2026-08-01-spec-tsk478-parent-portal.md):
        первый вход по magic-link/TG auto-назначает роль `student` в момент
        создания учётки — без этого шага родитель остался бы без роли
        `parent` и не прошёл бы гейт дашборда. Роль `student` НЕ снимается
        здесь автоматически (операционное решение оператора).

        Бросает: ValueError, если один из пользователей не найден.
        """
        await self.repo.add_link(db, parent_id, student_id)

        role = (
            await db.execute(select(Roles).where(Roles.name == _PARENT_ROLE_NAME))
        ).scalar_one_or_none()
        if role is not None:
            await self.roles_repo.add_role(db, parent_id, role.id)

    async def remove_link(self, db: AsyncSession, parent_id: int, student_id: int) -> None:
        """Удалить связь родитель↔ученик. Роль `parent` не трогается —
        снятие роли (если понадобится) отдельное решение оператора через
        существующий `DELETE /users/{id}/roles/{role_id}`."""
        await self.repo.remove_link(db, parent_id, student_id)
