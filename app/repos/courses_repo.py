# app/repos/courses_repo.py

from types import SimpleNamespace
from typing import Optional, List, Dict, Any, Tuple, Iterable, Set
from sqlalchemy import select, text, delete, insert, or_, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.courses import Courses
from app.models.association_tables import t_course_parents
from app.repos.base import BaseRepository


# tsk-662: ключ кеша дерева курса внутри `AsyncSession.info`. Сессия живёт
# ровно один запрос (`get_async_db` открывает её на запрос и закрывает), а
# крон — один тик, поэтому кеш не переживает запрос и не может «залипнуть»
# между ними. Один и тот же корень раньше обходился по нескольку раз за
# запрос: `resolve_next_item` считает и множество доступных курсов, и обход
# самого корня, а сводка занятия — на КАЖДОГО из 7-12 участников группы,
# у которых дерево одно и то же.
_TREE_CACHE_KEY = "tsk662_course_tree_cache"


def course_tree_cache(db: AsyncSession) -> Dict[int, List[int]]:
    """Кеш обхода дерева курса, живущий ровно столько же, сколько сессия."""
    cache = db.info.get(_TREE_CACHE_KEY)
    if cache is None:
        cache = {}
        db.info[_TREE_CACHE_KEY] = cache
    return cache


def invalidate_course_tree_cache(db: AsyncSession) -> None:
    """Сбросить кеш дерева: иерархия в этой сессии изменилась."""
    db.info.pop(_TREE_CACHE_KEY, None)


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

        ⚠️ Отдаёт ORM-объекты с подгруженными родителями (второй запрос) —
        это нужно API (`CourseRead.parent_course_ids`). Обходу дерева нужны
        только id/порядок: там `get_child_rows`, а не этот метод (tsk-662).
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

    async def get_child_rows(
        self,
        db: AsyncSession,
        course_id: int,
    ) -> List[Tuple[int, Optional[int], str]]:
        """Прямые дети курса для ОБХОДА дерева: `(id, order_number, title)`.

        Лёгкий брат `get_children` (tsk-662). Отличий два, оба про цену:

        1. Не грузит `parent_courses`. Родители узла обходу не нужны, а
           `selectinload` — это ВТОРОЙ запрос на каждый узел: обход дерева
           из 103 узлов стоил 206 запросов вместо 103.
        2. Не создаёт ORM-объекты `Courses` вовсе. Иначе частично
           загруженный курс осел бы в identity map сессии, и соседний код,
           отдающий этот же курс наружу, получил бы пустой
           `parent_course_ids` — свойство модели молча отдаёт `[]`, когда
           связь не загружена, то есть поле обнулилось бы БЕЗ ошибки.

        Сортировка та же, что у `get_children`: `order_number ASC NULLS
        LAST`, затем `id` — порядок обхода от замены метода не меняется.
        """
        stmt = (
            select(
                t_course_parents.c.course_id,
                t_course_parents.c.order_number,
                Courses.title,
            )
            .join(Courses, Courses.id == t_course_parents.c.course_id)
            .where(t_course_parents.c.parent_course_id == course_id)
            .order_by(
                t_course_parents.c.order_number.asc().nulls_last(),
                t_course_parents.c.course_id.asc(),
            )
        )
        result = await db.execute(stmt)
        return [(row[0], row[1], row[2]) for row in result.all()]

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
                       c.created_at, c.is_required, c.course_uid, c.is_public_demo
                FROM courses c
                INNER JOIN course_parents cp ON c.id = cp.course_id
                WHERE cp.parent_course_id = :course_id

                UNION ALL

                -- Рекурсивный случай: дети детей
                SELECT c.id, c.title, c.access_level, c.description,
                       c.created_at, c.is_required, c.course_uid, c.is_public_demo
                FROM courses c
                INNER JOIN course_parents cp ON c.id = cp.course_id
                INNER JOIN course_descendants cd ON cp.parent_course_id = cd.id
            )
            SELECT DISTINCT id, title, access_level, description,
                   created_at, is_required, course_uid, is_public_demo
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
                course_uid=row.course_uid,
                is_public_demo=row.is_public_demo,
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

    async def search_root_courses(
        self,
        db: AsyncSession,
        *,
        query: str,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Courses]:
        """
        Поиск ТОЛЬКО среди корневых курсов (без родителя) по title/course_uid (ILIKE).

        Подкурс графа (`course_parents`) не назначается ученику отдельно от
        родителя — вне родительского курса он не открывается (tsk-031, находка
        оператора 2026-07-25: поиск для UI-кнопки «Назначить курс» отдавал весь
        граф, включая подкурсы). Тот же outerjoin/IS NULL фильтр, что и в
        `get_root_courses`, плюс параметризованный ILIKE (экранирование
        `%`/`_`/`\\`, как в `BaseRepository.search_text`).
        """
        if not query:
            return []

        def _escape_like(val: str) -> str:
            return val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

        pattern = f"%{_escape_like(query)}%"
        stmt = (
            select(Courses)
            .outerjoin(t_course_parents, Courses.id == t_course_parents.c.course_id)
            .where(
                t_course_parents.c.course_id.is_(None),
                or_(
                    Courses.title.ilike(pattern, escape="\\"),
                    Courses.course_uid.ilike(pattern, escape="\\"),
                ),
            )
            .order_by(Courses.title)
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_course_tree(
        self,
        db: AsyncSession,
        course_id: int
    ) -> Optional[SimpleNamespace]:
        """
        Получить дерево курса с детьми (рекурсивная структура).

        Возвращает узел (обычный объект с атрибутами, читаемыми через
        `CourseTreeRead.model_validate(..., from_attributes=True)`), а не ORM-объект
        `Courses`: узлы дерева переиспользуются на разных уровнях вложенности (курс
        с несколькими родителями), а `child_courses`/`parent_courses` — двусторонний
        `relationship(back_populates=...)`. Присваивание в него (даже через
        `object.__setattr__`) не обходит дескриптор SQLAlchemy — он всё равно
        синхронизирует обратную сторону связи и в async-контексте ленивая подгрузка
        падает `MissingGreenlet` (tsk-463). Отдельная структура убирает эту связь
        совсем и заодно даёт полю верное имя `children`, которое ждёт схема
        `CourseTreeRead` (репозиторий раньше писал в `child_courses`).
        """
        # Получаем сам курс (BaseRepository.get грузит parent_courses селектом заранее)
        course = await self.get(db, course_id)
        if not course:
            return None

        # Получаем всех потомков рекурсивно одним запросом
        all_children = await self.get_all_children(db, course_id)

        # parent_id -> [child_id, ...] и child_id -> [parent_id, ...] из одного запроса
        children_ids_map: Dict[int, List[int]] = {}
        parent_ids_map: Dict[int, List[int]] = {}
        if all_children:
            child_ids = [c.id for c in all_children]
            query = text("""
                SELECT cp.parent_course_id, cp.course_id
                FROM course_parents cp
                WHERE cp.course_id = ANY(:child_ids)
            """)
            result = await db.execute(query, {"child_ids": child_ids})
            for parent_id, child_id in result.fetchall():
                children_ids_map.setdefault(parent_id, []).append(child_id)
                parent_ids_map.setdefault(child_id, []).append(parent_id)

        by_id: Dict[int, Courses] = {c.id: c for c in all_children}

        def build_node(course_obj: Courses, parent_course_ids: List[int]) -> SimpleNamespace:
            """Рекурсивно строит узел дерева без обращения к ORM-relationship'ам."""
            child_ids = children_ids_map.get(course_obj.id, [])
            return SimpleNamespace(
                id=course_obj.id,
                title=course_obj.title,
                access_level=course_obj.access_level,
                description=course_obj.description,
                parent_course_ids=parent_course_ids,
                created_at=course_obj.created_at,
                is_required=course_obj.is_required,
                course_uid=course_obj.course_uid,
                is_public_demo=course_obj.is_public_demo,
                children=[
                    build_node(by_id[child_id], parent_ids_map.get(child_id, []))
                    for child_id in child_ids
                ],
            )

        return build_node(course, course.parent_course_ids)
    
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

        # tsk-662: иерархия изменилась — кеш обхода этой сессии больше не верен.
        invalidate_course_tree_cache(db)
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
        # tsk-662: порядок подкурсов изменился — обход этой сессии пересчитать.
        invalidate_course_tree_cache(db)
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