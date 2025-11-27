from __future__ import annotations

from typing import Optional, Any, Dict, List, Sequence, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tasks import Tasks
from app.repos.tasks_repo import TasksRepository
from app.services.base import BaseService
from app.utils.exceptions import DomainError
from app.services.difficulty_levels_service import DifficultyLevelsService
from app.services.courses_service import CoursesService

class TasksService(BaseService[Tasks]):
    """
    Сервис для работы с заданиями.

    Базовый CRUD (create/get/update/delete/list/paginate) реализован
    в BaseService[Tasks]. Здесь добавляем доменные методы, связанные
    с импортом и внешним идентификатором.
    """

    def __init__(self, repo: TasksRepository = TasksRepository()) -> None:
        """
        Инициализирует сервис с репозиторием заданий.
        """
        super().__init__(repo)

    async def get_by_external_uid(
        self,
        db: AsyncSession,
        external_uid: str,
    ) -> Tasks:
        task = await self.repo.get_by_keys(
            db,
            {"external_uid": external_uid},
        )
        if task is None:
            raise DomainError(
                detail="Задача с указанным external_uid не найдена",
                status_code=404,
                payload={"external_uid": external_uid},
            )
        return task
    
    async def bulk_upsert(
        self,
        db: AsyncSession,
        items: Sequence[Dict[str, Any]],
    ) -> List[Tuple[str, str, int]]:
        """
        Массовый upsert задач по external_uid.

        Для каждого элемента:
        - если задача с таким external_uid не найдена → создаём (CREATE),
        - если найдена → обновляем поля (UPDATE).

        :param db: асинхронная сессия БД.
        :param items: список словарей с полями задачи
                      (external_uid, course_id, difficulty_id, task_content,
                       solution_rules, max_score).
        :return: список кортежей (external_uid, action, id), где
                 action ∈ {"created", "updated"}.
        """
        results: List[Tuple[str, str, int]] = []

        for data in items:
            external_uid = data["external_uid"]

            # Пытаемся найти существующую задачу по external_uid
            existing = await self.repo.get_by_keys(
                db,
                {"external_uid": external_uid},
            )

            if existing is None:
                # CREATE
                obj_in = {
                    "external_uid": external_uid,
                    "course_id": data["course_id"],
                    "difficulty_id": data["difficulty_id"],
                    "task_content": data["task_content"],
                    "solution_rules": data.get("solution_rules"),
                    "max_score": data.get("max_score"),
                }
                # используем базовый репозиторий для создания
                task = await self.repo.create(db, obj_in)
                results.append((external_uid, "created", task.id))
            else:
                # UPDATE — перезаписываем основные поля из импорта
                existing.course_id = data["course_id"]
                existing.difficulty_id = data["difficulty_id"]
                existing.task_content = data["task_content"]
                existing.solution_rules = data.get("solution_rules")
                existing.max_score = data.get("max_score")

                db.add(existing)
                await db.commit()
                await db.refresh(existing)

                results.append((external_uid, "updated", existing.id))

        return results
    
    async def validate_task_import(
            self,
            db: AsyncSession,
            *,
            task_content: Any,
            solution_rules: Any | None,
            difficulty_code: str | None,
            difficulty_id: int | None,
            course_code: str | None,
            external_uid: str | None,
        ) -> tuple[bool, List[str]]:
            """
            Предварительная валидация задания перед импортом.

            Проверяем:
            - наличие external_uid;
            - базовую структуру task_content (например, options[].id);
            - наличие ключевых полей в solution_rules (например, max_score);
            - существование difficulty по difficulty_code;
            - существование course по course_code.

            Ничего не записывает в БД, только возвращает список ошибок.
            """
            errors: List[str] = []

            # ---- external_uid ----
            if not external_uid:
                errors.append("external_uid not provided or empty")

            # ---- task_content ----
            if task_content is None:
                errors.append("task_content not provided")
            elif isinstance(task_content, dict):
                # Пример структурной проверки: options[].id missing
                options = task_content.get("options")
                if options is not None:
                    if not isinstance(options, list):
                        errors.append("options should be a list")
                    else:
                        missing_ids = [
                            idx for idx, opt in enumerate(options)
                            if not isinstance(opt, dict) or "id" not in opt
                        ]
                        if missing_ids:
                            errors.append("options[].id missing")
            else:
                errors.append("task_content should be an object (JSON)")

            # ---- solution_rules ----
            if solution_rules is None:
                errors.append("solution_rules not provided")
            elif isinstance(solution_rules, dict):
                # Пример бизнес-проверки: solution_rules.max_score not provided
                if "max_score" not in solution_rules:
                    errors.append("solution_rules.max_score not provided")
            else:
                errors.append("solution_rules should be an object (JSON)")

            # ---- difficulty_code / difficulty_id ----
            diff_service = DifficultyLevelsService()

            if difficulty_code:
                # Основной путь — проверка по коду сложности
                diff = await diff_service.repo.get_by_keys(db, {"code": difficulty_code})
                if diff is None:
                    errors.append(f"difficulty_id not found for code='{difficulty_code}'")
            elif difficulty_id is not None:
                # Резервный путь — если пришёл только ID сложности
                diff = await diff_service.get_by_id(db, difficulty_id)
                if diff is None:
                    errors.append(f"difficulty_id {difficulty_id} not found")
            else:
                errors.append("difficulty_code or difficulty_id not provided")

            # ---- course_code ----
            if not course_code:
                errors.append("course_code not provided")
            else:
                course_service = CoursesService()
                course = await course_service.repo.get_by_keys(
                    db,
                    {"course_uid": course_code},
                )
                if course is None:
                    errors.append(f"course_id not found for course_code='{course_code}'")

            is_valid = len(errors) == 0
            return is_valid, errors

    async def find_by_external_uids(
        self,
        db: AsyncSession,
        uids: list[str],
    ) -> list[tuple[str, int]]:
        """
        Массовый поиск задач по списку external_uid.

        Возвращает список кортежей (external_uid, id)
        только для тех uid, которые действительно найдены.
        """
        if not uids:
            return []

        stmt = (
            select(self.repo.model.external_uid, self.repo.model.id)
            .where(self.repo.model.external_uid.in_(uids))
        )

        rows = (await db.execute(stmt)).all()

        # rows: List[(external_uid, id)]
        return [(uid, id_) for uid, id_ in rows]

