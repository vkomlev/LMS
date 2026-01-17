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
from app.schemas.task_content import TaskContent
from app.schemas.solution_rules import SolutionRules

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

    def _validate_task_data(
        self,
        task_content: Any,
        solution_rules: Any | None,
    ) -> Tuple[TaskContent, SolutionRules]:
        """
        Валидирует task_content и solution_rules, проверяя их соответствие.
        
        Args:
            task_content: Содержимое задачи (dict или TaskContent).
            solution_rules: Правила проверки (dict или SolutionRules).
            
        Returns:
            Кортеж (TaskContent, SolutionRules) - валидированные схемы.
            
        Raises:
            DomainError: При ошибках валидации.
        """
        try:
            # Валидация task_content
            if isinstance(task_content, dict):
                task_content_obj = TaskContent.model_validate(task_content)
            elif isinstance(task_content, TaskContent):
                task_content_obj = task_content
            else:
                raise DomainError(
                    detail="task_content должен быть словарем или объектом TaskContent",
                    status_code=400,
                )
            
            # Валидация solution_rules
            if solution_rules is None:
                raise DomainError(
                    detail="solution_rules обязателен для создания задачи",
                    status_code=400,
                )
            
            if isinstance(solution_rules, dict):
                solution_rules_obj = SolutionRules.model_validate(solution_rules)
            elif isinstance(solution_rules, SolutionRules):
                solution_rules_obj = solution_rules
            else:
                raise DomainError(
                    detail="solution_rules должен быть словарем или объектом SolutionRules",
                    status_code=400,
                )
            
            # Валидация соответствия correct_options и options[].id
            solution_rules_obj.validate_with_task_content(task_content_obj)
            
            return task_content_obj, solution_rules_obj
            
        except ValueError as e:
            raise DomainError(
                detail=f"Ошибка валидации данных задачи: {str(e)}",
                status_code=400,
            ) from e

    def _sync_max_score(
        self,
        obj_in: Dict[str, Any],
        solution_rules: SolutionRules,
    ) -> Dict[str, Any]:
        """
        Синхронизирует max_score из solution_rules в tasks.max_score.
        
        Если max_score не указан в obj_in, берется из solution_rules.
        Если указан в обоих местах, проверяется соответствие.
        
        Args:
            obj_in: Словарь данных для создания/обновления задачи.
            solution_rules: Валидированные правила проверки.
            
        Returns:
            Обновленный obj_in с синхронизированным max_score.
            
        Raises:
            DomainError: При несоответствии max_score.
        """
        solution_max_score = solution_rules.max_score
        task_max_score = obj_in.get("max_score")
        
        if task_max_score is None:
            # Если max_score не указан в задаче, берем из solution_rules
            obj_in["max_score"] = solution_max_score
        elif task_max_score != solution_max_score:
            # Если указаны оба, но не совпадают - ошибка
            raise DomainError(
                detail=(
                    f"max_score не совпадает: tasks.max_score={task_max_score}, "
                    f"solution_rules.max_score={solution_max_score}. "
                    f"Значения должны быть одинаковыми."
                ),
                status_code=400,
            )
        
        return obj_in

    async def create(
        self, db: AsyncSession, obj_in: Dict[str, Any]
    ) -> Tasks:
        """
        Создать задачу с валидацией task_content и solution_rules.
        
        Переопределяет базовый метод для добавления валидации и синхронизации max_score.
        """
        task_content = obj_in.get("task_content")
        solution_rules = obj_in.get("solution_rules")
        
        if task_content is not None and solution_rules is not None:
            # Валидация и синхронизация
            task_content_obj, solution_rules_obj = self._validate_task_data(
                task_content, solution_rules
            )
            obj_in = self._sync_max_score(obj_in, solution_rules_obj)
            # Обновляем obj_in валидированными данными
            obj_in["task_content"] = task_content_obj.model_dump()
            obj_in["solution_rules"] = solution_rules_obj.model_dump()
        
        return await super().create(db, obj_in)

    async def update(
        self, db: AsyncSession, db_obj: Tasks, obj_in: Dict[str, Any]
    ) -> Tasks:
        """
        Обновить задачу с валидацией task_content и solution_rules.
        
        Переопределяет базовый метод для добавления валидации и синхронизации max_score.
        """
        task_content = obj_in.get("task_content")
        solution_rules = obj_in.get("solution_rules")
        
        # Если обновляются task_content или solution_rules, нужно валидировать
        if task_content is not None or solution_rules is not None:
            # Берем текущие значения, если не указаны новые
            if task_content is None:
                task_content = db_obj.task_content
            if solution_rules is None:
                solution_rules = db_obj.solution_rules
            
            if task_content is not None and solution_rules is not None:
                # Валидация и синхронизация
                task_content_obj, solution_rules_obj = self._validate_task_data(
                    task_content, solution_rules
                )
                obj_in = self._sync_max_score(obj_in, solution_rules_obj)
                # Обновляем obj_in валидированными данными
                obj_in["task_content"] = task_content_obj.model_dump()
                obj_in["solution_rules"] = solution_rules_obj.model_dump()
        
        return await super().update(db, db_obj, obj_in)

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

        Валидирует task_content и solution_rules перед сохранением.

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
            task_content = data.get("task_content")
            solution_rules = data.get("solution_rules")

            # Валидация и синхронизация max_score
            if task_content is not None and solution_rules is not None:
                task_content_obj, solution_rules_obj = self._validate_task_data(
                    task_content, solution_rules
                )
                # Синхронизируем max_score
                data = self._sync_max_score(data, solution_rules_obj)
                # Обновляем данные валидированными значениями
                data["task_content"] = task_content_obj.model_dump()
                data["solution_rules"] = solution_rules_obj.model_dump()

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
                # используем наш переопределенный create для валидации
                task = await self.create(db, obj_in)
                results.append((external_uid, "created", task.id))
            else:
                # UPDATE — перезаписываем основные поля из импорта
                obj_in = {
                    "course_id": data["course_id"],
                    "difficulty_id": data["difficulty_id"],
                    "task_content": data["task_content"],
                    "solution_rules": data.get("solution_rules"),
                    "max_score": data.get("max_score"),
                }
                # используем наш переопределенный update для валидации
                task = await self.update(db, existing, obj_in)
                results.append((external_uid, "updated", task.id))

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
            - базовую структуру task_content (через TaskContent схему);
            - наличие ключевых полей в solution_rules (через SolutionRules схему);
            - соответствие correct_options и options[].id;
            - уникальность options[].id;
            - существование difficulty по difficulty_code;
            - существование course по course_code.

            Ничего не записывает в БД, только возвращает список ошибок.
            """
            errors: List[str] = []

            # ---- external_uid ----
            if not external_uid:
                errors.append("external_uid not provided or empty")

            # ---- task_content и solution_rules ----
            if task_content is None:
                errors.append("task_content not provided")
            elif solution_rules is None:
                errors.append("solution_rules not provided")
            else:
                # Используем нашу валидацию для проверки структуры и соответствия
                try:
                    task_content_obj, solution_rules_obj = self._validate_task_data(
                        task_content, solution_rules
                    )
                    # Дополнительная проверка: валидация max_score
                    if solution_rules_obj.max_score <= 0:
                        errors.append("solution_rules.max_score must be positive")
                except DomainError as e:
                    errors.append(f"Validation error: {e.detail}")
                except Exception as e:
                    errors.append(f"Unexpected validation error: {str(e)}")

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

