# app/services/user_courses_service.py

from __future__ import annotations

from typing import Optional, List, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_courses import UserCourses
from app.repos.user_courses_repo import UserCoursesRepository
from app.services.base import BaseService
from app.utils.exceptions import DomainError


class UserCoursesService(BaseService[UserCourses]):
    """
    Сервис для связей пользователей с курсами.
    """
    def __init__(self, repo: UserCoursesRepository = UserCoursesRepository()):
        super().__init__(repo)

    async def get_user_courses(
        self,
        db: AsyncSession,
        user_id: int,
        order_by_order: bool = True,
    ) -> List[UserCourses]:
        """
        Получить курсы пользователя с сортировкой.

        :param db: асинхронная сессия БД.
        :param user_id: ID пользователя.
        :param order_by_order: Если True, сортировать по order_number, иначе по added_at.
        :return: Список связей пользователя с курсами.
        """
        return await self.repo.get_user_courses(db, user_id, order_by_order)

    async def assign_course_with_order(
        self,
        db: AsyncSession,
        user_id: int,
        course_id: int,
        order_number: Optional[int] = None,
    ) -> UserCourses:
        """
        Привязать курс к пользователю с указанием порядкового номера.
        Если order_number не указан, установится автоматически через триггер БД.

        :param db: асинхронная сессия БД.
        :param user_id: ID пользователя.
        :param course_id: ID курса.
        :param order_number: Порядковый номер (опционально, установится автоматически если None).
        :return: Созданная связь пользователя с курсом.
        """
        # Проверяем, не существует ли уже такая связь
        existing = await self.repo.get_by_keys(
            db,
            {"user_id": user_id, "course_id": course_id},
        )
        if existing:
            raise DomainError(
                detail="Курс уже привязан к пользователю",
                status_code=400,
                payload={"user_id": user_id, "course_id": course_id},
            )

        # Создаем связь (order_number установится триггером, если не указан)
        return await self.create(
            db,
            {
                "user_id": user_id,
                "course_id": course_id,
                "order_number": order_number,
            },
        )

    async def bulk_assign_courses(
        self,
        db: AsyncSession,
        user_id: int,
        course_ids: List[int],
    ) -> List[UserCourses]:
        """
        Массовая привязка курсов к пользователю.
        order_number установится автоматически для каждой записи через триггер БД.

        :param db: асинхронная сессия БД.
        :param user_id: ID пользователя.
        :param course_ids: Список ID курсов для привязки.
        :return: Список созданных связей пользователя с курсами.
        """
        return await self.repo.bulk_create_user_courses(db, user_id, course_ids)

    async def reorder_courses(
        self,
        db: AsyncSession,
        user_id: int,
        course_orders: List[Dict[str, int]],
    ) -> List[UserCourses]:
        """
        Переупорядочить курсы пользователя (явное обновление order_number).

        :param db: асинхронная сессия БД.
        :param user_id: ID пользователя.
        :param course_orders: Список словарей вида [{"course_id": 1, "order_number": 1}, ...].
        :return: Список обновленных связей пользователя с курсами.
        """
        return await self.repo.reorder_user_courses(db, user_id, course_orders)

    async def get_course_users(
        self,
        db: AsyncSession,
        course_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> List[UserCourses]:
        """
        Получить список пользователей (студентов) курса.

        :param db: асинхронная сессия БД.
        :param course_id: ID курса.
        :param limit: Максимум результатов.
        :param offset: Смещение.
        :return: Список связей пользователей с курсом.
        """
        return await self.repo.get_course_users(db, course_id, limit, offset)
