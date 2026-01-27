# app/repos/courses_repo.py

from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy import select, text, delete, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.courses import Courses
from app.models.association_tables import t_course_parents
from app.repos.base import BaseRepository


class CoursesRepository(BaseRepository[Courses]):
    """
    Репозиторий для курсов.
    Добавляйте здесь методы-спецы: иерархия, зависимости, и т.п.
    """
    def __init__(self) -> None:
        super().__init__(Courses)

    async def get_children(
        self,
        db: AsyncSession,
        course_id: int
    ) -> List[tuple[Courses, Optional[int]]]:
        """
        Получить прямых детей курса (потомки первого уровня).
        
        Возвращает список кортежей (course, order_number).
        Сортировка: по order_number (NULL в конце), затем по id.
        ⚠️ ВАЖНО: order_number автоматически управляется триггером БД.
        """
        stmt = (
            select(Courses, t_course_parents.c.order_number)
            .join(t_course_parents, Courses.id == t_course_parents.c.course_id)
            .where(t_course_parents.c.parent_course_id == course_id)
            .order_by(
                t_course_parents.c.order_number.asc().nulls_last(),
                Courses.id.asc()
            )
            .options(selectinload(Courses.parent_courses))
        )
        result = await db.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def get_all_children(
        self,
        db: AsyncSession,
        course_id: int
    ) -> List[Courses]:
        """
        Получить всех потомков курса рекурсивно (все уровни вложенности).
        Использует рекурсивный CTE запрос.
        """
        query = text("""
            WITH RECURSIVE course_descendants AS (
                -- Базовый случай: прямые дети
                SELECT c.id, c.title, c.access_level, c.description, 
                       c.created_at, c.is_required, c.course_uid
                FROM courses c
                INNER JOIN course_parents cp ON c.id = cp.course_id
                WHERE cp.parent_course_id = :course_id
                
                UNION ALL
                
                -- Рекурсивный случай: дети детей
                SELECT c.id, c.title, c.access_level, c.description,
                       c.created_at, c.is_required, c.course_uid
                FROM courses c
                INNER JOIN course_parents cp ON c.id = cp.course_id
                INNER JOIN course_descendants cd ON cp.parent_course_id = cd.id
            )
            SELECT DISTINCT id, title, access_level, description,
                   created_at, is_required, course_uid
            FROM course_descendants
            ORDER BY id
        """)
        
        result = await db.execute(query, {"course_id": course_id})
        rows = result.fetchall()
        
        # Преобразуем строки в объекты Courses
        courses = []
        for row in rows:
            course = Courses(
                id=row.id,
                title=row.title,
                access_level=row.access_level,
                description=row.description,
                created_at=row.created_at,
                is_required=row.is_required,
                course_uid=row.course_uid
            )
            courses.append(course)
        
        return courses

    async def get_root_courses(
        self,
        db: AsyncSession
    ) -> List[Courses]:
        """Получить корневые курсы (без родителей)."""
        # Курсы, которые не являются дочерними ни для одного курса
        stmt = (
            select(Courses)
            .outerjoin(t_course_parents, Courses.id == t_course_parents.c.course_id)
            .where(t_course_parents.c.course_id.is_(None))
            .options(selectinload(Courses.parent_courses))
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_course_tree(
        self,
        db: AsyncSession,
        course_id: int
    ) -> Optional[Courses]:
        """
        Получить дерево курса с детьми (рекурсивная структура).
        Возвращает курс с загруженными детьми всех уровней.
        Построение дерева выполняется в Python после получения всех курсов одним запросом.
        """
        # Получаем сам курс
        course = await self.get(db, course_id)
        if not course:
            return None
        
        # Получаем всех потомков рекурсивно одним запросом
        all_children = await self.get_all_children(db, course_id)
        
        # Создаем словарь для быстрого доступа: parent_id -> список детей
        children_map: Dict[int, List[Courses]] = {}
        
        # Группируем потомков по родителям (теперь у курса может быть несколько родителей)
        # Получаем все связи родитель-ребенок для всех потомков
        if all_children:
            child_ids = [c.id for c in all_children]
            query = text("""
                SELECT cp.parent_course_id, cp.course_id
                FROM course_parents cp
                WHERE cp.course_id = ANY(:child_ids)
            """)
            result = await db.execute(query, {"child_ids": child_ids})
            parent_child_pairs = result.fetchall()
            
            for parent_id, child_id in parent_child_pairs:
                if parent_id not in children_map:
                    children_map[parent_id] = []
                # Находим объект курса-ребенка
                child_course = next((c for c in all_children if c.id == child_id), None)
                if child_course:
                    children_map[parent_id].append(child_course)
        
        # Рекурсивно строим дерево
        # ВАЖНО: используем object.__setattr__ для установки relationship без триггера lazy loading
        def build_tree(course_obj: Courses) -> Courses:
            """Рекурсивно строит дерево для курса."""
            children = children_map.get(course_obj.id, [])
            # Используем object.__setattr__ для установки атрибута без триггера lazy loading
            # Это обходит механизм SQLAlchemy для lazy loading в async контексте
            built_children = [build_tree(child) for child in children]
            object.__setattr__(course_obj, 'child_courses', built_children)
            return course_obj
        
        return build_tree(course)
    
    async def set_parent_courses(
        self,
        db: AsyncSession,
        course_id: int,
        parent_course_ids: Optional[List[int]] = None,
        parent_courses: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """
        Установить родительские курсы для курса.
        Удаляет все существующие связи и создает новые.
        
        Args:
            course_id: ID курса
            parent_course_ids: Список ID родительских курсов (order_number будет установлен автоматически)
            parent_courses: Список словарей с ключами 'parent_course_id' и 'order_number' (опционально)
        
        ⚠️ ВАЖНО: order_number автоматически устанавливается триггером БД, если не указан.
        ⚠️ ТЕХНИЧЕСКИЙ ДОЛГ: При удалении связей вызывается sync_on_child_removed для синхронизации
        связей преподавателей с курсами (из-за ограничения PostgreSQL триггер отключен).
        См. docs/database-triggers-contract.md
        """
        # Получаем список существующих связей перед удалением
        existing_links_stmt = select(t_course_parents).where(
            t_course_parents.c.course_id == course_id
        )
        existing_links_result = await db.execute(existing_links_stmt)
        existing_links = existing_links_result.all()
        
        # Определяем новые родители
        new_parent_ids = set()
        if parent_courses is not None:
            new_parent_ids = {pc.get("parent_course_id") for pc in parent_courses}
        elif parent_course_ids is not None:
            new_parent_ids = set(parent_course_ids)
        
        # Удаляем все существующие связи
        stmt = delete(t_course_parents).where(t_course_parents.c.course_id == course_id)
        await db.execute(stmt)
        
        # ⚠️ ТЕХНИЧЕСКИЙ ДОЛГ: Синхронизируем связи преподавателей для удаленных связей
        # Вызываем sync_on_child_removed для каждой удаленной связи, которой нет в новых родителях
        from app.repos.teacher_courses_repo import TeacherCoursesRepository
        teacher_courses_repo = TeacherCoursesRepository()
        for link in existing_links:
            old_parent_id = link.parent_course_id
            if old_parent_id not in new_parent_ids:
                await teacher_courses_repo.sync_on_child_removed(
                    db, removed_course_id=course_id, removed_parent_id=old_parent_id
                )
        
        # Определяем, какие данные использовать
        if parent_courses is not None:
            # Используем parent_courses (с приоритетом)
            values = [
                {
                    "course_id": course_id,
                    "parent_course_id": pc.get("parent_course_id"),
                    "order_number": pc.get("order_number")  # Может быть None - триггер установит автоматически
                }
                for pc in parent_courses
            ]
        elif parent_course_ids is not None:
            # Используем parent_course_ids
            values = [
                {
                    "course_id": course_id,
                    "parent_course_id": pid,
                    "order_number": None  # Триггер установит автоматически
                }
                for pid in parent_course_ids
            ]
        else:
            values = []
        
        # Создаем новые связи
        if values:
            await db.execute(t_course_parents.insert().values(values))
        
        # Commit выполняется после синхронизации
        await db.commit()
    
    async def update_course_parent_order(
        self,
        db: AsyncSession,
        course_id: int,
        parent_course_id: int,
        order_number: Optional[int]
    ) -> None:
        """
        Обновить порядковый номер подкурса у конкретного родителя.
        
        ⚠️ ВАЖНО: Триггер БД автоматически пересчитает order_number остальных подкурсов.
        См. docs/database-triggers-contract.md
        """
        from sqlalchemy import update as sql_update
        
        stmt = (
            sql_update(t_course_parents)
            .where(
                (t_course_parents.c.course_id == course_id) &
                (t_course_parents.c.parent_course_id == parent_course_id)
            )
            .values(order_number=order_number)
        )
        await db.execute(stmt)
        await db.commit()