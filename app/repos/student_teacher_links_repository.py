# app/repos/student_teacher_links_repository.py

from typing import List

from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.association_tables import t_student_teacher_links
from app.models.users import Users


class StudentTeacherLinksRepository:
    """
    Репозиторий для операций many-to-many между студентами и преподавателями
    через таблицу student_teacher_links.
    """

    async def list_teachers(
        self,
        db: AsyncSession,
        student_id: int,
    ) -> List[Users]:
        """
        Вернуть всех преподавателей для заданного студента.
        """
        stmt = (
            select(Users)
            .join(
                t_student_teacher_links,
                Users.id == t_student_teacher_links.c.teacher_id,
            )
            .where(t_student_teacher_links.c.student_id == student_id)
        )
        res = await db.execute(stmt)
        return res.scalars().all()

    async def list_students(
        self,
        db: AsyncSession,
        teacher_id: int,
    ) -> List[Users]:
        """
        Вернуть всех студентов для заданного преподавателя.
        """
        stmt = (
            select(Users)
            .join(
                t_student_teacher_links,
                Users.id == t_student_teacher_links.c.student_id,
            )
            .where(t_student_teacher_links.c.teacher_id == teacher_id)
        )
        res = await db.execute(stmt)
        return res.scalars().all()

    async def add_link(
        self,
        db: AsyncSession,
        student_id: int,
        teacher_id: int,
    ) -> None:
        """
        Создать связь студент↔преподаватель.
        Если пользователь(и) не найдены — ValueError.
        Если связь уже есть — пропускаем (ON CONFLICT DO NOTHING).
        """
        # Проверяем, что оба пользователя существуют
        student = await db.get(Users, student_id)
        teacher = await db.get(Users, teacher_id)
        if not student or not teacher:
            raise ValueError("Student or Teacher not found")

        stmt = (
            insert(t_student_teacher_links)
            .values(student_id=student_id, teacher_id=teacher_id)
            .on_conflict_do_nothing(
                index_elements=["student_id", "teacher_id"],
            )
        )
        await db.execute(stmt)
        await db.commit()

    async def remove_link(
        self,
        db: AsyncSession,
        student_id: int,
        teacher_id: int,
    ) -> None:
        """
        Удалить связь студент↔преподаватель.
        Если связи нет — просто ничего не делаем.
        """
        stmt = (
            delete(t_student_teacher_links)
            .where(
                t_student_teacher_links.c.student_id == student_id,
                t_student_teacher_links.c.teacher_id == teacher_id,
            )
        )
        await db.execute(stmt)
        await db.commit()
