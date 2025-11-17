# app/services/student_teacher_links_service.py

from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.users import Users
from app.repos.student_teacher_links_repository import (
    StudentTeacherLinksRepository,
)


class StudentTeacherLinksService:
    """
    Сервис для работы со связями студент↔преподаватель.
    """

    def __init__(
        self,
        repo: StudentTeacherLinksRepository | None = None,
    ) -> None:
        self.repo = repo or StudentTeacherLinksRepository()

    async def list_teachers(
        self,
        db: AsyncSession,
        student_id: int,
    ) -> List[Users]:
        """
        Вернуть всех преподавателей для студента.
        """
        return await self.repo.list_teachers(db, student_id)

    async def list_students(
        self,
        db: AsyncSession,
        teacher_id: int,
    ) -> List[Users]:
        """
        Вернуть всех студентов для преподавателя.
        """
        return await self.repo.list_students(db, teacher_id)

    async def add_link(
        self,
        db: AsyncSession,
        student_id: int,
        teacher_id: int,
    ) -> None:
        """
        Создать связь студент↔преподаватель.

        Бросает:
            ValueError: если один из пользователей не найден.
        """
        await self.repo.add_link(db, student_id, teacher_id)

    async def remove_link(
        self,
        db: AsyncSession,
        student_id: int,
        teacher_id: int,
    ) -> None:
        """
        Удалить связь студент↔преподаватель.
        """
        await self.repo.remove_link(db, student_id, teacher_id)
