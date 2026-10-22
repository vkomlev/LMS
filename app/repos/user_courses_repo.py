# app/repos/user_courses_repo.py

from typing import List, Optional, Dict
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_courses import UserCourses
from app.repos.base import BaseRepository


#: Порядок курсов ученика — как в ``GET /users/{id}/courses``: ``order_number``
#: (NULL в конце), затем время привязки и ``course_id``. tsk-996: им же
#: упорядочены ответы bulk-привязки и reorder, раньше шедшие по ``course_id``.
_COURSE_ORDER = (
    UserCourses.order_number.asc().nulls_last(),
    UserCourses.added_at.asc(),
    UserCourses.course_id.asc(),
)


class UserCoursesRepository(BaseRepository[UserCourses]):
    """
    Репозиторий для связей пользователей с курсами.
    
    ⚠️ ВАЖНО: Бизнес-логика для order_number реализована в БД через триггеры:
    - trg_set_user_course_order_number (автоматическая нумерация)
    - trg_reorder_after_delete (пересчет после удаления)
    Не дублировать логику автоматической нумерации в коде!
    См. docs/database-triggers-contract.md
    """
    def __init__(self) -> None:
        super().__init__(UserCourses)

    async def get_user_courses(
        self,
        db: AsyncSession,
        user_id: int,
        order_by_order: bool = True
    ) -> List[UserCourses]:
        """
        Получить курсы пользователя с сортировкой.
        
        Args:
            db: Сессия БД
            user_id: ID пользователя
            order_by_order: Если True, сортировать по order_number (NULLS LAST), 
                          иначе по added_at
        
        Returns:
            Список связей пользователя с курсами
        """
        stmt = select(UserCourses).where(UserCourses.user_id == user_id)
        
        # tsk-996: course_id — добивка; движок (resolve_next_item без корня)
        # обходит курсы в этом порядке, и при равных ключах «следующий» прыгал.
        if order_by_order:
            # Сортировка по order_number (NULL в конце)
            stmt = stmt.order_by(*_COURSE_ORDER)
        else:
            stmt = stmt.order_by(UserCourses.added_at.asc(), UserCourses.course_id.asc())
        
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def bulk_create_user_courses(
        self,
        db: AsyncSession,
        user_id: int,
        course_ids: List[int]
    ) -> List[UserCourses]:
        """
        Массовая привязка курсов к пользователю.
        order_number установится автоматически через триггер БД, если не указан явно.
        
        Args:
            db: Сессия БД
            user_id: ID пользователя
            course_ids: Список ID курсов для привязки
        
        Returns:
            Список созданных связей пользователя с курсами
        """
        # Проверяем, какие курсы уже привязаны
        existing_stmt = select(UserCourses.course_id).where(
            UserCourses.user_id == user_id,
            UserCourses.course_id.in_(course_ids)
        )
        existing_result = await db.execute(existing_stmt)
        existing_course_ids = {row[0] for row in existing_result.fetchall()}
        
        # Создаем только новые связи
        # dict.fromkeys — без повторов: [5, 5] дал бы две вставки одного PK (500)
        new_course_ids = list(dict.fromkeys(
            cid for cid in course_ids if cid not in existing_course_ids
        ))
        
        if new_course_ids:
            # Создаем новые связи (order_number будет установлен триггером автоматически)
            objs_in = [
                {"user_id": user_id, "course_id": course_id, "order_number": None}
                for course_id in new_course_ids
            ]
            await self.batch_create(db, objs_in)

        # Новые и уже привязанные курсы — одним списком в порядке GET (tsk-996)
        stmt = select(UserCourses).where(
            UserCourses.user_id == user_id,
            UserCourses.course_id.in_(course_ids)
        ).order_by(*_COURSE_ORDER).execution_options(populate_existing=True)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def reorder_user_courses(
        self,
        db: AsyncSession,
        user_id: int,
        course_orders: List[Dict[str, int]]
    ) -> List[UserCourses]:
        """
        Переупорядочить курсы пользователя (обновление order_number вручную).
        
        Args:
            db: Сессия БД
            user_id: ID пользователя
            course_orders: Список словарей вида [{"course_id": 1, "order_number": 1}, ...]
        
        Returns:
            Список обновленных связей пользователя с курсами
        """
        # Обновляем order_number для каждого курса
        for order_item in course_orders:
            course_id = order_item["course_id"]
            order_number = order_item["order_number"]
            
            stmt = (
                update(UserCourses)
                .where(
                    UserCourses.user_id == user_id,
                    UserCourses.course_id == course_id
                )
                .values(order_number=order_number)
            )
            await db.execute(stmt)
        
        await db.commit()
        
        # Возвращаем обновленные записи
        updated_course_ids = [item["course_id"] for item in course_orders]
        stmt = select(UserCourses).where(
            UserCourses.user_id == user_id,
            UserCourses.course_id.in_(updated_course_ids)
        ).order_by(*_COURSE_ORDER).execution_options(populate_existing=True)
        # tsk-996: порядок как в GET /users/{id}/courses; populate_existing —
        # order_number из базы, а не из закешированных сессией объектов
        # (expire_on_commit=False, UPDATE выше идёт мимо identity map).
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_course_users(
        self,
        db: AsyncSession,
        course_id: int,
        limit: int = 100,
        offset: int = 0
    ) -> List[UserCourses]:
        """
        Получить список пользователей (студентов) курса.
        
        Args:
            db: Сессия БД
            course_id: ID курса
            limit: Максимум результатов
            offset: Смещение
        
        Returns:
            Список связей пользователей с курсом
        """
        stmt = (
            select(UserCourses)
            .where(UserCourses.course_id == course_id)
            .order_by(UserCourses.added_at.asc(), UserCourses.user_id.asc())  # tsk-996
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())