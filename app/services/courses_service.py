# app/services/courses_service.py

from __future__ import annotations

from typing import Optional, List, Dict, Any, Sequence, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError, DBAPIError
from sqlalchemy.orm import selectinload
from sqlalchemy import select

from app.models.courses import Courses
from app.repos.courses_repo import CoursesRepository
from app.repos.course_dependencies_repository import CourseDependenciesRepository
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
    
    async def create(
        self, db: AsyncSession, obj_in: Dict[str, Any]
    ) -> Courses:
        """
        Создать курс с обработкой parent_course_ids.
        
        parent_course_ids обрабатывается отдельно, так как это relationship, а не поле модели.
        """
        parent_course_ids = obj_in.pop("parent_course_ids", None)
        parent_courses = obj_in.pop("parent_courses", None)
        # Создаем курс без parent_course_ids и parent_courses
        course = await super().create(db, obj_in)
        # Устанавливаем родительские курсы
        if parent_courses is not None or parent_course_ids is not None:
            # Преобразуем parent_courses в список словарей, если это Pydantic модели
            parent_courses_dict = None
            if parent_courses is not None:
                parent_courses_dict = [
                    pc.model_dump() if hasattr(pc, 'model_dump') else pc
                    for pc in parent_courses
                ]
            await self.repo.set_parent_courses(
                db, course.id,
                parent_course_ids=parent_course_ids,
                parent_courses=parent_courses_dict
            )
        # Перезагружаем курс с relationships через новый запрос
        stmt = select(Courses).where(Courses.id == course.id).options(selectinload(Courses.parent_courses))
        result = await db.execute(stmt)
        course = result.scalar_one()
        return course
    
    async def update(
        self, db: AsyncSession, db_obj: Courses, obj_in: Dict[str, Any]
    ) -> Courses:
        """
        Обновить курс с обработкой parent_course_ids.
        
        parent_course_ids обрабатывается отдельно, так как это relationship, а не поле модели.
        """
        parent_course_ids = obj_in.pop("parent_course_ids", None)
        parent_courses = obj_in.pop("parent_courses", None)
        # Обновляем родительские курсы ПЕРЕД обновлением основного объекта
        # чтобы все изменения были в одной транзакции
        if parent_courses is not None or parent_course_ids is not None:
            # Преобразуем parent_courses в список словарей, если это Pydantic модели
            parent_courses_dict = None
            if parent_courses is not None:
                parent_courses_dict = [
                    pc.model_dump() if hasattr(pc, 'model_dump') else pc
                    for pc in parent_courses
                ]
            await self.repo.set_parent_courses(
                db, db_obj.id,
                parent_course_ids=parent_course_ids,
                parent_courses=parent_courses_dict
            )
        # Обновляем курс без parent_course_ids
        course = await super().update(db, db_obj, obj_in)
        # Перезагружаем курс с relationships через новый запрос
        stmt = select(Courses).where(Courses.id == course.id).options(selectinload(Courses.parent_courses))
        result = await db.execute(stmt)
        course = result.scalar_one()
        return course

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
        new_parent_ids: Optional[List[int]],
    ) -> None:
        """
        Упрощенная валидация иерархии курсов.
        
        ⚠️ ВАЖНО: Основная проверка циклов выполняется триггером БД 
        (trg_check_course_hierarchy_cycle). Не дублировать логику проверки циклов!
        См. docs/database-triggers-contract.md
        
        Здесь проверяем только существование курсов.

        :param db: асинхронная сессия БД.
        :param course_id: ID курса для перемещения.
        :param new_parent_ids: Список ID новых родителей (None или [] для корневого курса).
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

        # Если указаны родители, проверяем их существование
        if new_parent_ids:
            for parent_id in new_parent_ids:
                parent = await self.get_by_id(db, parent_id)
                if parent is None:
                    raise DomainError(
                        detail="Родительский курс не найден",
                        status_code=404,
                        payload={"parent_course_id": parent_id},
                    )

    async def move_course(
        self,
        db: AsyncSession,
        course_id: int,
        new_parent_ids: Optional[List[int]] = None,
        new_parent_courses: Optional[List[Dict[str, Any]]] = None,
    ) -> Courses:
        """
        Переместить курс в иерархии (изменить родительские курсы).
        
        ⚠️ ВАЖНО: Валидация циклов выполняется триггером БД 
        (trg_check_course_hierarchy_cycle). Не дублировать логику проверки циклов!
        См. docs/database-triggers-contract.md

        :param db: асинхронная сессия БД.
        :param course_id: ID курса для перемещения.
        :param new_parent_ids: Список ID новых родителей (None или [] для корневого курса).
        :param new_parent_courses: Список словарей с ключами 'parent_course_id' и 'order_number'.
        :return: Обновленный курс.
        :raises DomainError: если курс не найден, родитель не найден, или обнаружен цикл.
        """
        # Определяем, какие данные использовать для валидации
        parent_ids_for_validation = new_parent_ids
        if new_parent_courses is not None:
            parent_ids_for_validation = [pc.get("parent_course_id") for pc in new_parent_courses]
        
        # Валидация существования курсов
        await self.validate_hierarchy(db, course_id, parent_ids_for_validation)

        # Получаем курс
        course = await self.get_by_id(db, course_id)
        if course is None:
            raise DomainError(
                detail="Курс не найден",
                status_code=404,
                payload={"course_id": course_id},
            )

        try:
            # Преобразуем new_parent_courses в список словарей, если это Pydantic модели
            parent_courses_dict = None
            if new_parent_courses is not None:
                parent_courses_dict = [
                    pc.model_dump() if hasattr(pc, 'model_dump') else pc
                    for pc in new_parent_courses
                ]
            
            # Устанавливаем родительские курсы через репозиторий
            await self.repo.set_parent_courses(
                db, course_id,
                parent_course_ids=new_parent_ids,
                parent_courses=parent_courses_dict
            )
            # Обновляем объект в сессии
            await db.refresh(course)
            return course
        except (IntegrityError, DBAPIError) as e:
            # Обрабатываем ошибки от триггера БД
            error_msg = str(e.orig) if hasattr(e, 'orig') else str(e)
            
            if "Circular reference detected" in error_msg:
                raise DomainError(
                    detail="Нельзя создать цикл в иерархии курсов",
                    status_code=400,
                    payload={"course_id": course_id, "new_parent_ids": new_parent_ids},
                ) from e
            elif "cannot be its own parent" in error_msg or "Course cannot be its own parent" in error_msg:
                raise DomainError(
                    detail="Курс не может быть родителем самому себе",
                    status_code=400,
                    payload={"course_id": course_id},
                ) from e
            # Пробрасываем другие ошибки как есть
            raise

    async def bulk_upsert(
        self,
        db: AsyncSession,
        items: Sequence[Dict[str, Any]],
        dependencies_map: Optional[Dict[str, List[str]]] = None,
    ) -> Tuple[List[Tuple[str, str, int]], List[DomainError]]:
        """
        Массовый upsert курсов по course_uid.

        Для каждого элемента:
        - если курс с таким course_uid не найден → создаём (CREATE),
        - если найден → обновляем поля (UPDATE).

        Обрабатывает иерархию (parent_course_uid преобразуется в parent_course_ids).
        После импорта всех курсов обрабатывает зависимости (если передан dependencies_map).

        :param db: асинхронная сессия БД.
        :param items: список словарей с полями курса
                      (course_uid, title, description, access_level, 
                       parent_course_uid, is_required).
                      parent_course_uid может быть строкой (course_uid родителя) или None.
                      Для множественных родителей используйте parent_course_uids (список).
        :param dependencies_map: словарь {course_uid: [required_course_uid, ...]} для зависимостей.
        :return: кортеж (results, errors), где
                 results - список кортежей (course_uid, action, course_id), где
                 action ∈ {"created", "updated"},
                 errors - список DomainError для курсов, которые не удалось импортировать.
        """
        results: List[Tuple[str, str, int]] = []
        errors: List[DomainError] = []
        deps_repo = CourseDependenciesRepository()

        # Сначала создаем/обновляем все курсы
        for data in items:
            course_uid = data["course_uid"]
            parent_course_uid = data.get("parent_course_uid")
            parent_course_uids = data.get("parent_course_uids", [])
            
            # Преобразуем parent_course_uid/parent_course_uids в parent_course_ids
            parent_course_ids = []
            if parent_course_uid:
                # Обратная совместимость: один родитель
                parent_course_uids = [parent_course_uid]
            
            if parent_course_uids:
                for uid in parent_course_uids:
                    try:
                        parent_course = await self.get_by_course_uid(db, uid)
                        parent_course_ids.append(parent_course.id)
                    except DomainError:
                        # Родительский курс не найден - добавляем в ошибки и пропускаем этот курс
                        errors.append(DomainError(
                            detail=f"Родительский курс с course_uid '{uid}' не найден",
                            status_code=400,
                            payload={"course_uid": course_uid, "parent_course_uid": uid},
                        ))
                        continue

            try:
                # Пытаемся найти существующий курс по course_uid
                existing: Optional[Courses] = await self.repo.get_by_keys(
                    db,
                    {"course_uid": course_uid},
                )

                if existing is None:
                    # CREATE
                    obj_in = {
                        "course_uid": course_uid,
                        "title": data["title"],
                        "description": data.get("description"),
                        "access_level": data["access_level"],
                        "parent_course_ids": parent_course_ids if parent_course_ids else None,
                        "is_required": data.get("is_required", False),
                    }
                    course = await self.create(db, obj_in)
                    results.append((course_uid, "created", course.id))
                else:
                    # UPDATE — перезаписываем основные поля из импорта
                    obj_in = {
                        "title": data["title"],
                        "description": data.get("description"),
                        "access_level": data["access_level"],
                        "parent_course_ids": parent_course_ids if parent_course_ids else [],
                        "is_required": data.get("is_required", False),
                    }
                    course = await self.update(db, existing, obj_in)
                    results.append((course_uid, "updated", course.id))
            except Exception as e:
                # Ошибка при создании/обновлении курса - добавляем в ошибки
                errors.append(DomainError(
                    detail=f"Ошибка при импорте курса '{course_uid}': {str(e)}",
                    status_code=400,
                    payload={"course_uid": course_uid},
                ))
                continue

        # Обрабатываем зависимости после импорта всех курсов
        if dependencies_map:
            for course_uid, required_courses_uid_list in dependencies_map.items():
                # Находим курс по course_uid
                try:
                    course = await self.get_by_course_uid(db, course_uid)
                except DomainError:
                    # Курс не найден - пропускаем зависимости для него
                    continue

                # Для каждой зависимости находим required_course и добавляем связь
                for required_course_uid in required_courses_uid_list:
                    try:
                        required_course = await self.get_by_course_uid(db, required_course_uid)
                        # Проверяем, что это не self-dependency
                        if course.id == required_course.id:
                            continue
                        # Добавляем зависимость (пропускаем, если уже существует)
                        await deps_repo.add_dependency(db, course.id, required_course.id)
                    except DomainError:
                        # Зависимый курс не найден - пропускаем
                        continue
                    except Exception:
                        # Ошибка при добавлении зависимости - пропускаем
                        continue

        return results, errors
