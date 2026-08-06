# app/repos/course_dependencies_repository.py

from typing import List
from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courses import Courses
from app.models.association_tables import t_course_dependencies


class CourseDependenciesRepository:
    """
    Репозиторий для работы с зависимостями курсов через таблицу course_dependencies.
    
    ⚠️ ВАЖНО: Предотвращение самоссылок реализовано в БД через CHECK CONSTRAINT 
    (check_no_self_dependency). Не дублировать логику проверки в коде!
    См. docs/database-triggers-contract.md
    """

    async def list_dependencies(
        self, db: AsyncSession, course_id: int
    ) -> List[Courses]:
        """
        Вернуть список курсов, от которых зависит данный course_id.
        """
        stmt = (
            select(Courses)
            .join(
                t_course_dependencies,
                Courses.id == t_course_dependencies.c.required_course_id
            )
            .where(t_course_dependencies.c.course_id == course_id)
        )
        res = await db.execute(stmt)
        return res.scalars().all()

    async def is_required_elsewhere(
        self, db: AsyncSession, course_id: int
    ) -> bool:
        """
        Выступает ли course_id обязательным условием (required_course_id)
        хотя бы для одной другой зависимости — независимо от глубины: и
        course_id, и зависимый курс могут быть подкурсами, не только корнями.
        """
        stmt = (
            select(t_course_dependencies.c.course_id)
            .where(t_course_dependencies.c.required_course_id == course_id)
            .limit(1)
        )
        res = await db.execute(stmt)
        return res.first() is not None

    async def add_dependency(
        self, db: AsyncSession, course_id: int, required_course_id: int,
        auto_assign: bool = True,
    ) -> None:
        """
        Добавить зависимость: course_id зависит от required_course_id.
        Пропускаем, если уже существует.

        auto_assign=False (tsk-231) — зависимость выдаётся точечно: требуемый
        курс не раздаётся автоматически и блокирует только тех, кому назначен.
        """
        # Проверяем, что оба курса существуют
        course = await db.get(Courses, course_id)
        req_course = await db.get(Courses, required_course_id)
        if not course or not req_course:
            raise ValueError("One or both courses not found")

        stmt = (
            insert(t_course_dependencies)
            .values(
                course_id=course_id,
                required_course_id=required_course_id,
                auto_assign=auto_assign,
            )
            .on_conflict_do_nothing(index_elements=["course_id", "required_course_id"])
        )
        await db.execute(stmt)
        await db.commit()

    async def remove_dependency(
        self, db: AsyncSession, course_id: int, required_course_id: int
    ) -> None:
        """
        Удалить зависимость course_id → required_course_id.
        """
        stmt = (
            delete(t_course_dependencies)
            .where(
                t_course_dependencies.c.course_id == course_id,
                t_course_dependencies.c.required_course_id == required_course_id,
            )
        )
        await db.execute(stmt)
        await db.commit()

    async def bulk_add_dependencies(
        self,
        db: AsyncSession,
        course_id: int,
        required_course_ids: List[int],
        auto_assign: bool = True,
    ) -> List[Courses]:
        """
        Массовое добавление зависимостей: course_id зависит от всех курсов из списка.
        Пропускает уже существующие зависимости и self-dependency.
        Возвращает список успешно добавленных зависимостей.

        auto_assign применяется ко всем связям вызова (tsk-231): смешивать
        режимы в одном bulk-запросе незачем — методист добавляет либо
        пререквизиты курса, либо точечные мини-курсы.
        """
        # Проверяем, что курс существует
        course = await db.get(Courses, course_id)
        if not course:
            raise ValueError(f"Course {course_id} not found")
        
        # Проверяем существование всех required_courses
        stmt = select(Courses).where(Courses.id.in_(required_course_ids))
        result = await db.execute(stmt)
        existing_courses = {c.id: c for c in result.scalars().all()}
        
        # Фильтруем: убираем несуществующие курсы и self-dependency
        valid_course_ids = [
            rid for rid in required_course_ids
            if rid in existing_courses and rid != course_id
        ]
        
        if not valid_course_ids:
            return []
        
        # Массовое добавление зависимостей (пропускаем конфликты)
        values = [
            {
                "course_id": course_id,
                "required_course_id": rid,
                "auto_assign": auto_assign,
            }
            for rid in valid_course_ids
        ]
        
        stmt = (
            insert(t_course_dependencies)
            .values(values)
            .on_conflict_do_nothing(index_elements=["course_id", "required_course_id"])
        )
        await db.execute(stmt)
        await db.commit()
        
        # Возвращаем список успешно добавленных зависимостей
        return [existing_courses[rid] for rid in valid_course_ids]