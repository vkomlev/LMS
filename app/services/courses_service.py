# app/services/courses_service.py

from __future__ import annotations

from typing import Optional, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError, DBAPIError

from app.models.courses import Courses
from app.repos.courses_repo import CoursesRepository
from app.services.base import BaseService
from app.utils.exceptions import DomainError


class CoursesService(BaseService[Courses]):
    """
    Сервис для работы с курсами.

    Базовый CRUD реализован в BaseService[Courses].
    Здесь добавляем доменные методы, связанные с импортом и внешним кодом курса.
    """

    def __init__(self, repo: CoursesRepository = CoursesRepository()) -> None:
        super().__init__(repo)

    async def get_by_course_uid(
        self,
        db: AsyncSession,
        course_uid: str,
    ) -> Courses:
        """
        Найти курс по его внешнему коду (course_uid).

        :param db: асинхронная сессия БД.
        :param course_uid: внешний код курса (например, 'COURSE-PY-01').
        :return: ORM-объект Courses.
        :raises DomainError: если курс с таким course_uid не найден.
        """
        course: Optional[Courses] = await self.repo.get_by_keys(
            db,
            {"course_uid": course_uid},
        )
        if course is None:
            raise DomainError(
                detail="Курс с указанным кодом не найден",
                status_code=404,
                payload={"course_uid": course_uid},
            )
        return course

    async def get_children(
        self,
        db: AsyncSession,
        course_id: int,
    ) -> List[Courses]:
        """
        Получить прямых детей курса (потомки первого уровня).

        :param db: асинхронная сессия БД.
        :param course_id: ID курса.
        :return: Список прямых детей курса.
        """
        return await self.repo.get_children(db, course_id)

    async def get_course_tree(
        self,
        db: AsyncSession,
        course_id: int,
    ) -> Optional[Courses]:
        """
        Получить дерево курса с детьми всех уровней (рекурсивная структура).

        :param db: асинхронная сессия БД.
        :param course_id: ID курса.
        :return: Курс с загруженными детьми всех уровней или None, если курс не найден.
        """
        return await self.repo.get_course_tree(db, course_id)

    async def get_root_courses(
        self,
        db: AsyncSession,
    ) -> List[Courses]:
        """
        Получить корневые курсы (без родителя).

        :param db: асинхронная сессия БД.
        :return: Список корневых курсов.
        """
        return await self.repo.get_root_courses(db)

    async def validate_hierarchy(
        self,
        db: AsyncSession,
        course_id: int,
        new_parent_id: Optional[int],
    ) -> None:
        """
        Упрощенная валидация иерархии курсов.
        Основная проверка циклов выполняется триггером БД.
        Здесь проверяем только существование курсов.

        :param db: асинхронная сессия БД.
        :param course_id: ID курса для перемещения.
        :param new_parent_id: ID нового родителя (None для корневого курса).
        :raises DomainError: если курс не найден или родитель не найден.
        """
        # Проверяем существование курса
        course = await self.get_by_id(db, course_id)
        if course is None:
            raise DomainError(
                detail="Курс не найден",
                status_code=404,
                payload={"course_id": course_id},
            )

        # Если указан родитель, проверяем его существование
        if new_parent_id is not None:
            parent = await self.get_by_id(db, new_parent_id)
            if parent is None:
                raise DomainError(
                    detail="Родительский курс не найден",
                    status_code=404,
                    payload={"parent_course_id": new_parent_id},
                )

    async def move_course(
        self,
        db: AsyncSession,
        course_id: int,
        new_parent_id: Optional[int],
    ) -> Courses:
        """
        Переместить курс в иерархии (изменить parent_course_id).
        Валидация циклов выполняется триггером БД.

        :param db: асинхронная сессия БД.
        :param course_id: ID курса для перемещения.
        :param new_parent_id: ID нового родителя (None для корневого курса).
        :return: Обновленный курс.
        :raises DomainError: если курс не найден, родитель не найден, или обнаружен цикл.
        """
        # Валидация существования курсов
        await self.validate_hierarchy(db, course_id, new_parent_id)

        # Получаем курс
        course = await self.get_by_id(db, course_id)
        if course is None:
            raise DomainError(
                detail="Курс не найден",
                status_code=404,
                payload={"course_id": course_id},
            )

        try:
            # Обновляем parent_course_id
            updated_course = await self.update(
                db,
                course,
                {"parent_course_id": new_parent_id},
            )
            return updated_course
        except (IntegrityError, DBAPIError) as e:
            # Обрабатываем ошибки от триггера БД
            error_msg = str(e.orig) if hasattr(e, 'orig') else str(e)
            
            if "Circular reference detected" in error_msg:
                raise DomainError(
                    detail="Нельзя создать цикл в иерархии курсов",
                    status_code=400,
                    payload={"course_id": course_id, "new_parent_id": new_parent_id},
                ) from e
            elif "cannot be its own parent" in error_msg or "Course cannot be its own parent" in error_msg:
                raise DomainError(
                    detail="Курс не может быть родителем самому себе",
                    status_code=400,
                    payload={"course_id": course_id},
                ) from e
            # Пробрасываем другие ошибки как есть
            raise
