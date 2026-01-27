# app/services/teacher_courses_service.py

from typing import List, Tuple, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.users import Users
from app.models.courses import Courses
from app.repos.teacher_courses_repo import TeacherCoursesRepository
from app.utils.exceptions import DomainError


class TeacherCoursesService:
    """
    Сервис для работы со связями преподавателей с курсами.
    
    ⚠️ ВАЖНО: Большая часть бизнес-логики реализована в БД через триггеры:
    - trg_auto_link_teacher_course_children (автоматическая привязка детей при привязке родителя)
    - trg_auto_unlink_teacher_course_children (автоматическая отвязка детей при отвязке родителя)
    - trg_sync_teacher_courses_on_child_added (синхронизация при добавлении ребенка)
    
    ⚠️ ТЕХНИЧЕСКИЙ ДОЛГ: Синхронизация при удалении ребенка реализована в коде
    (TeacherCoursesRepository.sync_on_child_removed) из-за ограничения PostgreSQL.
    См. docs/database-triggers-contract.md
    """
    
    def __init__(self, repo: TeacherCoursesRepository | None = None):
        self.repo = repo or TeacherCoursesRepository()
    
    async def list_teachers(
        self,
        db: AsyncSession,
        course_id: int,
        skip: int = 0,
        limit: int = 100,
        sort_by: str = "linked_at",
        order: str = "desc"
    ) -> Tuple[List[Users], int]:
        """
        Получить список преподавателей курса с пагинацией и сортировкой.
        
        Args:
            db: Сессия БД
            course_id: ID курса
            skip: Количество записей для пропуска
            limit: Максимальное количество записей
            sort_by: Поле для сортировки (linked_at, email, full_name)
            order: Направление сортировки (asc, desc)
        
        Returns:
            (items, total): список преподавателей и общее количество
        """
        from app.models.association_tables import t_teacher_courses
        
        # Строим запрос с JOIN для сортировки
        stmt = (
            select(Users)
            .join(t_teacher_courses, Users.id == t_teacher_courses.c.teacher_id)
            .where(t_teacher_courses.c.course_id == course_id)
        )
        
        # Сортировка
        if sort_by == "email":
            if order.lower() == "asc":
                stmt = stmt.order_by(Users.email.asc())
            else:
                stmt = stmt.order_by(Users.email.desc())
        elif sort_by == "full_name":
            if order.lower() == "asc":
                stmt = stmt.order_by(Users.full_name.asc().nulls_last())
            else:
                stmt = stmt.order_by(Users.full_name.desc().nulls_last())
        else:  # linked_at (по умолчанию)
            if order.lower() == "asc":
                stmt = stmt.order_by(t_teacher_courses.c.linked_at.asc())
            else:
                stmt = stmt.order_by(t_teacher_courses.c.linked_at.desc())
        
        # Пагинация
        stmt = stmt.offset(skip).limit(limit)
        
        result = await db.execute(stmt)
        items = list(result.scalars().all())
        
        # Подсчет общего количества
        total = await self.repo.count_teachers_by_course(db, course_id)
        
        return items, total
    
    async def list_courses(
        self,
        db: AsyncSession,
        teacher_id: int,
        skip: int = 0,
        limit: int = 100,
        sort_by: str = "linked_at",
        order: str = "desc"
    ) -> Tuple[List[Courses], int]:
        """
        Получить список курсов преподавателя с пагинацией и сортировкой.
        
        Args:
            db: Сессия БД
            teacher_id: ID преподавателя
            skip: Количество записей для пропуска
            limit: Максимальное количество записей
            sort_by: Поле для сортировки (linked_at, title, created_at)
            order: Направление сортировки (asc, desc)
        
        Returns:
            (items, total): список курсов и общее количество
        """
        from app.models.association_tables import t_teacher_courses
        
        # Строим запрос с JOIN для сортировки
        stmt = (
            select(Courses)
            .join(t_teacher_courses, Courses.id == t_teacher_courses.c.course_id)
            .where(t_teacher_courses.c.teacher_id == teacher_id)
        )
        
        # Сортировка
        if sort_by == "title":
            if order.lower() == "asc":
                stmt = stmt.order_by(Courses.title.asc())
            else:
                stmt = stmt.order_by(Courses.title.desc())
        elif sort_by == "created_at":
            if order.lower() == "asc":
                stmt = stmt.order_by(Courses.created_at.asc())
            else:
                stmt = stmt.order_by(Courses.created_at.desc())
        else:  # linked_at (по умолчанию)
            if order.lower() == "asc":
                stmt = stmt.order_by(t_teacher_courses.c.linked_at.asc())
            else:
                stmt = stmt.order_by(t_teacher_courses.c.linked_at.desc())
        
        # Пагинация
        stmt = stmt.offset(skip).limit(limit)
        
        result = await db.execute(stmt)
        items = list(result.scalars().all())
        
        # Подсчет общего количества
        total = await self.repo.count_courses_by_teacher(db, teacher_id)
        
        return items, total
    
    async def add_link(
        self,
        db: AsyncSession,
        teacher_id: int,
        course_id: int
    ) -> bool:
        """
        Привязать преподавателя к курсу.
        
        ⚠️ ВАЖНО: Триггер БД автоматически привяжет всех детей курса.
        Не нужно вручную привязывать детей!
        
        Args:
            db: Сессия БД
            teacher_id: ID преподавателя
            course_id: ID курса
        
        Returns:
            True если связь создана, False если уже существует
        
        Raises:
            DomainError: если преподаватель или курс не найдены
        """
        # Проверяем существование преподавателя
        teacher = await db.get(Users, teacher_id)
        if not teacher:
            raise DomainError(f"Преподаватель с ID {teacher_id} не найден")
        
        # Проверяем существование курса
        course = await db.get(Courses, course_id)
        if not course:
            raise DomainError(f"Курс с ID {course_id} не найден")
        
        return await self.repo.add_link(db, teacher_id, course_id)
    
    async def remove_link(
        self,
        db: AsyncSession,
        teacher_id: int,
        course_id: int
    ) -> bool:
        """
        Отвязать преподавателя от курса.
        
        ⚠️ ВАЖНО: Триггер БД автоматически отвяжет всех детей курса.
        Не нужно вручную отвязывать детей!
        
        Args:
            db: Сессия БД
            teacher_id: ID преподавателя
            course_id: ID курса
        
        Returns:
            True если связь удалена, False если не существовала
        """
        return await self.repo.remove_link(db, teacher_id, course_id)
