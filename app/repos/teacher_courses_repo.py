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
    
    ⚠️ ВАЖНО: Большая часть бизнес-логики реализована в БД через триггеры:
    - trg_auto_link_teacher_course_children (автоматическая привязка детей при привязке родителя)
    - trg_auto_unlink_teacher_course_children (автоматическая отвязка детей при отвязке родителя)
    - trg_sync_teacher_courses_on_child_added (синхронизация при добавлении ребенка)
    
    ⚠️ ТЕХНИЧЕСКИЙ ДОЛГ: Синхронизация при удалении ребенка из иерархии реализована в коде
    (метод sync_on_child_removed) из-за ограничения PostgreSQL:
    PostgreSQL не позволяет изменять таблицу teacher_courses в AFTER DELETE триггере на course_parents,
    если teacher_courses используется в запросе триггера (TriggeredDataChangeViolationError).
    
    Не дублировать логику автоматической синхронизации в коде!
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
    
    async def sync_on_child_removed(
        self,
        db: AsyncSession,
        removed_course_id: int,
        removed_parent_id: int,
        other_parent_ids: Optional[List[int]] = None,
        descendant_course_ids: Optional[List[int]] = None
    ) -> None:
        """
        Синхронизация связей преподавателей с курсами при удалении ребенка из иерархии.
        
        ⚠️ ТЕХНИЧЕСКИЙ ДОЛГ: Этот метод реализован в коде из-за ограничения PostgreSQL.
        PostgreSQL не позволяет изменять таблицу teacher_courses в AFTER DELETE триггере на course_parents,
        если teacher_courses используется в запросе триггера (TriggeredDataChangeViolationError).
        
        Логика:
        1. Найти всех преподавателей, привязанных к удаляемому родителю
        2. Проверить, есть ли у курса другие родители
        3. Если есть другие родители - проверить, привязаны ли преподаватели к ним
        4. Удалить связи для преподавателей, которые не привязаны к другим родителям
        5. Удалить связи для всех потомков удаляемого ребенка
        
        Args:
            db: Сессия БД
            removed_course_id: ID курса, который был удален из иерархии
            removed_parent_id: ID родителя, от которого был удален курс
            other_parent_ids: Список ID других родителей курса (опционально, для оптимизации)
        """
        from app.models.association_tables import t_teacher_courses, t_course_parents
        
        # 1. Получаем ID преподавателей родительского курса
        parent_teachers_stmt = select(t_teacher_courses.c.teacher_id).where(
            t_teacher_courses.c.course_id == removed_parent_id
        ).distinct()
        parent_teachers_result = await db.execute(parent_teachers_stmt)
        parent_teacher_ids = [row[0] for row in parent_teachers_result.all()]
        
        if not parent_teacher_ids:
            return  # Нет преподавателей у родителя - ничего не делаем
        
        # 2. Проверяем, есть ли у курса другие родители
        # Используем переданный список, если он есть, иначе читаем из БД
        if other_parent_ids is None:
            other_parents_stmt = select(t_course_parents.c.parent_course_id).where(
                t_course_parents.c.course_id == removed_course_id,
                t_course_parents.c.parent_course_id != removed_parent_id
            ).distinct()
            other_parents_result = await db.execute(other_parents_stmt)
            other_parent_ids = [row[0] for row in other_parents_result.all()]
        else:
            other_parent_ids = other_parent_ids
        
        # 3. Получаем ID преподавателей, привязанных к удаляемому курсу
        course_teachers_stmt = select(t_teacher_courses.c.teacher_id).where(
            t_teacher_courses.c.course_id == removed_course_id
        ).distinct()
        course_teachers_result = await db.execute(course_teachers_stmt)
        course_teacher_ids = [row[0] for row in course_teachers_result.all()]
        
        # 4. Если есть другие родители, проверяем привязку преподавателей к ним
        if other_parent_ids and course_teacher_ids:
            # Находим преподавателей, привязанных к другим родителям
            other_parent_teachers_stmt = select(t_teacher_courses.c.teacher_id).where(
                t_teacher_courses.c.course_id.in_(other_parent_ids),
                t_teacher_courses.c.teacher_id.in_(course_teacher_ids)
            ).distinct()
            other_parent_teachers_result = await db.execute(other_parent_teachers_stmt)
            teachers_with_other_parents = {row[0] for row in other_parent_teachers_result.all()}
            
            # Удаляем связи только для преподавателей, которые НЕ привязаны к другим родителям
            teachers_to_remove = [
                tid for tid in course_teacher_ids
                if tid not in teachers_with_other_parents
            ]
        else:
            # Нет других родителей - удаляем всех преподавателей
            teachers_to_remove = course_teacher_ids
        
        # 5. Удаляем связи для самого курса
        if teachers_to_remove:
            delete_course_stmt = delete(t_teacher_courses).where(
                t_teacher_courses.c.course_id == removed_course_id,
                t_teacher_courses.c.teacher_id.in_(teachers_to_remove)
            )
            await db.execute(delete_course_stmt)
        
        # 6. Находим всех потомков удаляемого ребенка (рекурсивно, с ограничением глубины)
        # Используем переданный список, если он есть, иначе читаем из БД
        if descendant_course_ids is None:
            descendants_stmt = text("""
                WITH RECURSIVE course_descendants AS (
                    SELECT cp.course_id, 1 as depth
                    FROM course_parents cp
                    WHERE cp.parent_course_id = :removed_course_id
                    
                    UNION ALL
                    
                    SELECT cp.course_id, cd.depth + 1
                    FROM course_parents cp
                    INNER JOIN course_descendants cd ON cp.parent_course_id = cd.course_id
                    WHERE cd.depth < 20
                )
                SELECT course_id FROM course_descendants
            """)
            descendants_result = await db.execute(
                descendants_stmt,
                {"removed_course_id": removed_course_id}
            )
            descendant_course_ids = [row[0] for row in descendants_result.all()]
        # Иначе используем переданный список
        
        # 7. Удаляем связи для всех потомков
        if descendant_course_ids and parent_teacher_ids:
            delete_descendants_stmt = delete(t_teacher_courses).where(
                t_teacher_courses.c.teacher_id.in_(parent_teacher_ids),
                t_teacher_courses.c.course_id.in_(descendant_course_ids)
            )
            await db.execute(delete_descendants_stmt)
        
        # Примечание: commit НЕ вызывается здесь, так как этот метод вызывается
        # из транзакции, которая сама управляет commit (например, из set_parent_courses)
