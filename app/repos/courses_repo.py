# app/repos/courses_repo.py

from typing import Optional, List, Dict
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courses import Courses
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
    ) -> List[Courses]:
        """Получить прямых детей курса (потомки первого уровня)."""
        stmt = select(Courses).where(Courses.parent_course_id == course_id)
        result = await db.execute(stmt)
        return list(result.scalars().all())

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
                SELECT id, title, access_level, description, parent_course_id, 
                       created_at, is_required, course_uid
                FROM courses
                WHERE parent_course_id = :course_id
                
                UNION ALL
                
                -- Рекурсивный случай: дети детей
                SELECT c.id, c.title, c.access_level, c.description, c.parent_course_id,
                       c.created_at, c.is_required, c.course_uid
                FROM courses c
                INNER JOIN course_descendants cd ON c.parent_course_id = cd.id
            )
            SELECT id, title, access_level, description, parent_course_id,
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
                parent_course_id=row.parent_course_id,
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
        """Получить корневые курсы (без родителя)."""
        stmt = select(Courses).where(Courses.parent_course_id.is_(None))
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
        
        # Группируем потомков по parent_course_id
        for child in all_children:
            parent_id = child.parent_course_id
            if parent_id is not None:
                if parent_id not in children_map:
                    children_map[parent_id] = []
                children_map[parent_id].append(child)
        
        # Рекурсивно строим дерево
        # ВАЖНО: используем object.__setattr__ для установки relationship без триггера lazy loading
        def build_tree(course_obj: Courses) -> Courses:
            """Рекурсивно строит дерево для курса."""
            children = children_map.get(course_obj.id, [])
            # Используем object.__setattr__ для установки атрибута без триггера lazy loading
            # Это обходит механизм SQLAlchemy для lazy loading в async контексте
            built_children = [build_tree(child) for child in children]
            object.__setattr__(course_obj, 'parent_course_reverse', built_children)
            return course_obj
        
        return build_tree(course)