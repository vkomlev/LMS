# app/repos/teacher_courses_repo.py

from typing import List, Optional, TYPE_CHECKING
from sqlalchemy import select, delete, text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from sqlalchemy import Table

from app.repos.base import BaseRepository


class TeacherCoursesRepository:
    """
    Репозиторий для связей преподавателей с курсами.
    
    ⚠️ ВАЖНО: Привязка преподавателей возможна только к курсам без родителей.
    Проверка выполняется на уровне БД через триггер trg_check_teacher_course_no_parents.
    
    См. docs/database-triggers-contract.md
    """
    
    async def list(
        self,
        db: AsyncSession,
        skip: int = 0,
        limit: int = 100,
        teacher_id: Optional[int] = None,
        course_id: Optional[int] = None,
        sort_by: str = "linked_at",
        order: str = "desc"
    ) -> List[tuple]:
        """
        Получить список связей преподавателей с курсами с пагинацией и сортировкой.
        
        Args:
            db: Сессия БД
            skip: Количество записей для пропуска
            limit: Максимальное количество записей
            teacher_id: Фильтр по ID преподавателя (опционально)
            course_id: Фильтр по ID курса (опционально)
            sort_by: Поле для сортировки (linked_at, teacher_id, course_id)
            order: Направление сортировки (asc, desc)
        
        Returns:
            Список кортежей (teacher_id, course_id, linked_at)
        """
        from app.models.association_tables import t_teacher_courses
        
        stmt = select(
            t_teacher_courses.c.teacher_id,
            t_teacher_courses.c.course_id,
            t_teacher_courses.c.linked_at
        )
        
        if teacher_id:
            stmt = stmt.where(t_teacher_courses.c.teacher_id == teacher_id)
        if course_id:
            stmt = stmt.where(t_teacher_courses.c.course_id == course_id)
        
        # Сортировка
        sort_column = getattr(t_teacher_courses.c, sort_by, t_teacher_courses.c.linked_at)
        if order.lower() == "asc":
            stmt = stmt.order_by(sort_column.asc())
        else:
            stmt = stmt.order_by(sort_column.desc())
        
        stmt = stmt.offset(skip).limit(limit)
        result = await db.execute(stmt)
        return result.all()
    
    async def get_link(
        self,
        db: AsyncSession,
        teacher_id: int,
        course_id: int
    ) -> Optional[tuple]:
        """
        Получить связь преподавателя с курсом.
        
        Args:
            db: Сессия БД
            teacher_id: ID преподавателя
            course_id: ID курса
        
        Returns:
            Кортеж (teacher_id, course_id, linked_at) или None если связь не найдена
        """
        from app.models.association_tables import t_teacher_courses
        
        stmt = select(
            t_teacher_courses.c.teacher_id,
            t_teacher_courses.c.course_id,
            t_teacher_courses.c.linked_at
        ).where(
            t_teacher_courses.c.teacher_id == teacher_id,
            t_teacher_courses.c.course_id == course_id
        )
        result = await db.execute(stmt)
        row = result.first()
        return row if row else None
    
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
        """
        stmt = text("""
            INSERT INTO teacher_courses (teacher_id, course_id)
            VALUES (:teacher_id, :course_id)
            ON CONFLICT (teacher_id, course_id) DO NOTHING
            RETURNING teacher_id, course_id
        """)
        result = await db.execute(stmt, {"teacher_id": teacher_id, "course_id": course_id})
        await db.commit()
        return result.rowcount > 0
    
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
        from app.models.association_tables import t_teacher_courses
        
        stmt = delete(t_teacher_courses).where(
            t_teacher_courses.c.teacher_id == teacher_id,
            t_teacher_courses.c.course_id == course_id
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount > 0
    
    async def get_link(
        self,
        db: AsyncSession,
        teacher_id: int,
        course_id: int
    ) -> Optional[tuple]:
        """
        Получить связь преподавателя с курсом.
        
        Args:
            db: Сессия БД
            teacher_id: ID преподавателя
            course_id: ID курса
        
        Returns:
            Кортеж (teacher_id, course_id, linked_at) или None если связь не найдена
        """
        from app.models.association_tables import t_teacher_courses
        
        stmt = select(
            t_teacher_courses.c.teacher_id,
            t_teacher_courses.c.course_id,
            t_teacher_courses.c.linked_at
        ).where(
            t_teacher_courses.c.teacher_id == teacher_id,
            t_teacher_courses.c.course_id == course_id
        )
        result = await db.execute(stmt)
        return result.first()
    
    # Алиасы для обратной совместимости
    async def add(
        self,
        db: AsyncSession,
        teacher_id: int,
        course_id: int
    ) -> bool:
        """Алиас для add_link (обратная совместимость)."""
        return await self.add_link(db, teacher_id, course_id)
    
    async def remove(
        self,
        db: AsyncSession,
        teacher_id: int,
        course_id: int
    ) -> bool:
        """Алиас для remove_link (обратная совместимость)."""
        return await self.remove_link(db, teacher_id, course_id)
    
    async def exists(
        self,
        db: AsyncSession,
        teacher_id: int,
        course_id: int
    ) -> bool:
        """Проверить существование связи."""
        from app.models.association_tables import t_teacher_courses
        
        stmt = select(t_teacher_courses).where(
            t_teacher_courses.c.teacher_id == teacher_id,
            t_teacher_courses.c.course_id == course_id
        )
        result = await db.execute(stmt)
        return result.first() is not None
    
    async def list_teachers_by_course(
        self,
        db: AsyncSession,
        course_id: int,
        skip: int = 0,
        limit: int = 100,
        sort_by: str = "linked_at",
        order: str = "desc"
    ) -> List[tuple]:
        """
        Получить список преподавателей курса с пагинацией и сортировкой.
        
        Args:
            db: Сессия БД
            course_id: ID курса
            skip: Количество записей для пропуска
            limit: Максимальное количество записей
            sort_by: Поле для сортировки (linked_at, teacher_id)
            order: Направление сортировки (asc, desc)
        
        Returns:
            Список кортежей (teacher_id, course_id, linked_at)
        """
        from app.models.association_tables import t_teacher_courses
        
        stmt = select(
            t_teacher_courses.c.teacher_id,
            t_teacher_courses.c.course_id,
            t_teacher_courses.c.linked_at
        ).where(t_teacher_courses.c.course_id == course_id)
        
        # Сортировка
        if sort_by == "teacher_id":
            if order.lower() == "asc":
                stmt = stmt.order_by(t_teacher_courses.c.teacher_id.asc())
            else:
                stmt = stmt.order_by(t_teacher_courses.c.teacher_id.desc())
        else:  # linked_at (по умолчанию)
            if order.lower() == "asc":
                stmt = stmt.order_by(t_teacher_courses.c.linked_at.asc())
            else:
                stmt = stmt.order_by(t_teacher_courses.c.linked_at.desc())
        
        stmt = stmt.offset(skip).limit(limit)
        result = await db.execute(stmt)
        return result.all()
    
    async def list_courses_by_teacher(
        self,
        db: AsyncSession,
        teacher_id: int,
        skip: int = 0,
        limit: int = 100,
        sort_by: str = "linked_at",
        order: str = "desc"
    ) -> List[tuple]:
        """
        Получить список курсов преподавателя с пагинацией и сортировкой.
        
        Args:
            db: Сессия БД
            teacher_id: ID преподавателя
            skip: Количество записей для пропуска
            limit: Максимальное количество записей
            sort_by: Поле для сортировки (linked_at, course_id)
            order: Направление сортировки (asc, desc)
        
        Returns:
            Список кортежей (teacher_id, course_id, linked_at)
        """
        from app.models.association_tables import t_teacher_courses
        
        stmt = select(
            t_teacher_courses.c.teacher_id,
            t_teacher_courses.c.course_id,
            t_teacher_courses.c.linked_at
        ).where(t_teacher_courses.c.teacher_id == teacher_id)
        
        # Сортировка
        if sort_by == "course_id":
            if order.lower() == "asc":
                stmt = stmt.order_by(t_teacher_courses.c.course_id.asc())
            else:
                stmt = stmt.order_by(t_teacher_courses.c.course_id.desc())
        else:  # linked_at (по умолчанию)
            if order.lower() == "asc":
                stmt = stmt.order_by(t_teacher_courses.c.linked_at.asc())
            else:
                stmt = stmt.order_by(t_teacher_courses.c.linked_at.desc())
        
        stmt = stmt.offset(skip).limit(limit)
        result = await db.execute(stmt)
        return result.all()
    
    async def get_teachers_by_course(
        self,
        db: AsyncSession,
        course_id: int,
        skip: int = 0,
        limit: int = 100
    ) -> List[int]:
        """Получить список ID преподавателей, привязанных к курсу (для обратной совместимости)."""
        from app.models.association_tables import t_teacher_courses
        
        stmt = select(t_teacher_courses.c.teacher_id).where(
            t_teacher_courses.c.course_id == course_id
        ).offset(skip).limit(limit)
        result = await db.execute(stmt)
        return [row[0] for row in result.all()]
    
    async def get_courses_by_teacher(
        self,
        db: AsyncSession,
        teacher_id: int,
        skip: int = 0,
        limit: int = 100
    ) -> List[int]:
        """Получить список ID курсов, привязанных к преподавателю (для обратной совместимости)."""
        from app.models.association_tables import t_teacher_courses
        
        stmt = select(t_teacher_courses.c.course_id).where(
            t_teacher_courses.c.teacher_id == teacher_id
        ).offset(skip).limit(limit)
        result = await db.execute(stmt)
        return [row[0] for row in result.all()]
    
    async def count_teachers_by_course(
        self,
        db: AsyncSession,
        course_id: int
    ) -> int:
        """Подсчитать количество преподавателей курса."""
        from app.models.association_tables import t_teacher_courses
        
        stmt = select(text("COUNT(*)")).select_from(t_teacher_courses).where(
            t_teacher_courses.c.course_id == course_id
        )
        result = await db.execute(stmt)
        return result.scalar() or 0
    
    async def count_courses_by_teacher(
        self,
        db: AsyncSession,
        teacher_id: int
    ) -> int:
        """Подсчитать количество курсов преподавателя."""
        from app.models.association_tables import t_teacher_courses
        
        stmt = select(text("COUNT(*)")).select_from(t_teacher_courses).where(
            t_teacher_courses.c.teacher_id == teacher_id
        )
        result = await db.execute(stmt)
        return result.scalar() or 0
    
    async def count(
        self,
        db: AsyncSession,
        teacher_id: Optional[int] = None,
        course_id: Optional[int] = None
    ) -> int:
        """Подсчитать количество связей (общий метод)."""
        from app.models.association_tables import t_teacher_courses
        
        stmt = select(text("COUNT(*)")).select_from(t_teacher_courses)
        if teacher_id:
            stmt = stmt.where(t_teacher_courses.c.teacher_id == teacher_id)
        if course_id:
            stmt = stmt.where(t_teacher_courses.c.course_id == course_id)
        result = await db.execute(stmt)
        return result.scalar() or 0
