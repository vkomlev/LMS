# app/repos/parent_student_links_repository.py

from typing import List

from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.association_tables import t_parent_student_links
from app.models.users import Users


class ParentStudentLinksRepository:
    """
    Репозиторий для операций many-to-many между родителями и учениками через
    таблицу parent_student_links (tsk-478, кабинет родителя). Паттерн — прямая
    копия StudentTeacherLinksRepository.
    """

    async def list_children(
        self,
        db: AsyncSession,
        parent_id: int,
    ) -> List[Users]:
        """
        Вернуть всех учеников, привязанных к родителю.
        """
        stmt = (
            select(Users)
            .join(
                t_parent_student_links,
                Users.id == t_parent_student_links.c.student_id,
            )
            .where(t_parent_student_links.c.parent_id == parent_id)
        )
        res = await db.execute(stmt)
        return res.scalars().all()

    async def list_parents(
        self,
        db: AsyncSession,
        student_id: int,
    ) -> List[Users]:
        """
        Вернуть всех родителей, привязанных к ученику.
        """
        stmt = (
            select(Users)
            .join(
                t_parent_student_links,
                Users.id == t_parent_student_links.c.parent_id,
            )
            .where(t_parent_student_links.c.student_id == student_id)
        )
        res = await db.execute(stmt)
        return res.scalars().all()

    async def is_linked(
        self,
        db: AsyncSession,
        parent_id: int,
        student_id: int,
    ) -> bool:
        """
        Есть ли связка (родитель видит именно этого ученика). Используется
        гейтом `GET /students/{id}/dashboard` — IDOR-критичная проверка.
        """
        stmt = select(t_parent_student_links.c.parent_id).where(
            t_parent_student_links.c.parent_id == parent_id,
            t_parent_student_links.c.student_id == student_id,
        )
        return (await db.execute(stmt)).first() is not None

    async def add_link(
        self,
        db: AsyncSession,
        parent_id: int,
        student_id: int,
    ) -> None:
        """
        Создать связь родитель↔ученик.
        Если пользователь(и) не найдены — ValueError.
        Если связь уже есть — пропускаем (ON CONFLICT DO NOTHING).
        """
        parent = await db.get(Users, parent_id)
        student = await db.get(Users, student_id)
        if not parent or not student:
            raise ValueError("Parent or Student not found")

        stmt = (
            insert(t_parent_student_links)
            .values(parent_id=parent_id, student_id=student_id)
            .on_conflict_do_nothing(
                index_elements=["parent_id", "student_id"],
            )
        )
        await db.execute(stmt)
        await db.commit()

    async def remove_link(
        self,
        db: AsyncSession,
        parent_id: int,
        student_id: int,
    ) -> None:
        """
        Удалить связь родитель↔ученик.
        Если связи нет — просто ничего не делаем.
        """
        stmt = (
            delete(t_parent_student_links)
            .where(
                t_parent_student_links.c.parent_id == parent_id,
                t_parent_student_links.c.student_id == student_id,
            )
        )
        await db.execute(stmt)
        await db.commit()
