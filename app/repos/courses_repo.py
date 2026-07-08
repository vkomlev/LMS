# app/repos/courses_repo.py

from typing import Optional, List, Dict, Any, Tuple, Iterable, Set
from sqlalchemy import select, text, delete, insert, update as sql_update
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
        parent_courses: Optional[List[Dict[str, Any]]] = None,
        replace: bool = False
    ) -> None:
        """
        Установить родительские курсы для курса.
        
        Args:
            course_id: ID курса
            parent_course_ids: Список ID родительских курсов (order_number будет установлен автоматически)
            parent_courses: Список словарей с ключами 'parent_course_id' и 'order_number' (опционально)
            replace: Если True, заменяет все существующие связи новыми. Если False, добавляет новые к существующим.
        
        ⚠️ ВАЖНО: order_number автоматически устанавливается триггером БД, если не указан.
        ⚠️ ВАЖНО: Привязка преподавателей и студентов возможна только к курсам без родителей.
        Проверка выполняется на уровне БД через триггеры.
        """
        # Текущие связи читаем всегда (нужны и для replace, и для добавления).
        existing_links_stmt = select(t_course_parents).where(
            t_course_parents.c.course_id == course_id
        )
        existing_links = (await db.execute(existing_links_stmt)).all()
        existing_parent_ids = {link.parent_course_id for link in existing_links}

        # Целевые родители из аргументов.
        if parent_courses is not None:
            desired_parent_ids = {pc.get("parent_course_id") for pc in parent_courses}
        elif parent_course_ids is not None:
            desired_parent_ids = set(parent_course_ids)
        else:
            desired_parent_ids = set()

        # Какие связи добавить (INSERT). Заполняется ниже с учётом replace.
        parents_to_insert: set = desired_parent_ids - existing_parent_ids

        if replace:
            to_remove = list(existing_parent_ids - desired_parent_ids)
            to_add = list(desired_parent_ids - existing_parent_ids)
            # tsk-174: НЕ делаем bulk `DELETE FROM course_parents` — он каскадит
            # AFTER-DELETE триггер пересчёта order_number в re-entrancy (asyncpg
            # TriggeredDataChangeViolationError: "tuple to be updated was already
            # modified by an operation triggered by the current command"). Вместо
            # DELETE+INSERT переносим родителя in-place: UPDATE parent_course_id
            # НЕ меняет order_number → триггер видит NEW.order_number == OLD и выходит
            # рано (без пересчёта соседей и без каскада).
            swaps = min(len(to_remove), len(to_add))
            for i in range(swaps):
                await db.execute(
                    sql_update(t_course_parents)
                    .where(
                        t_course_parents.c.course_id == course_id,
                        t_course_parents.c.parent_course_id == to_remove[i],
                    )
                    .values(parent_course_id=to_add[i])
                )
            # Остаток удаляемых (нет пары под swap) — точечный DELETE конкретного ребра.
            for pid in to_remove[swaps:]:
                await db.execute(
                    delete(t_course_parents).where(
                        t_course_parents.c.course_id == course_id,
                        t_course_parents.c.parent_course_id == pid,
                    )
                )
            # После swap'ов эти пары уже добавлены — из INSERT их убираем.
            parents_to_insert = set(to_add[swaps:])

        # Если нечего вставлять — коммитим и выходим (swap'ы/удаления уже применены).
        if not parents_to_insert:
            await db.commit()
            return

        # Данные для INSERT новых связей (триггер синхронизирует связи преподавателей).
        if parent_courses is not None:
            values = [
                {
                    "course_id": course_id,
                    "parent_course_id": pc.get("parent_course_id"),
                    "order_number": pc.get("order_number"),  # None → триггер проставит
                }
                for pc in parent_courses
                if pc.get("parent_course_id") in parents_to_insert
            ]
        elif parent_course_ids is not None:
            values = [
                {
                    "course_id": course_id,
                    "parent_course_id": pid,
                    "order_number": None,  # триггер проставит
                }
                for pid in parent_course_ids
                if pid in parents_to_insert
            ]
        else:
            values = []

        if values:
            await db.execute(t_course_parents.insert().values(values))

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

    async def filter_existing_ids(
        self,
        db: AsyncSession,
        course_ids: Iterable[int],
    ) -> Set[int]:
        """Возвращает подмножество id курсов, которые реально есть в БД (один запрос IN)."""
        ids = list({int(x) for x in course_ids})
        if not ids:
            return set()
        stmt = select(Courses.id).where(Courses.id.in_(ids))
        result = await db.execute(stmt)
        return {row[0] for row in result.all()}