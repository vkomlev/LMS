from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, case

from app.models.task_results import TaskResults
from app.repos.task_results_repo import TaskResultsRepository
from app.schemas.checking import StudentAnswer, CheckResult
from app.schemas.task_results import TaskResultCreate
from app.services.base import BaseService
from app.services.checking_service import CheckingService


class TaskResultsService(BaseService[TaskResults]):
    """
    Сервис для результатов выполнения заданий.
    """
    def __init__(
        self,
        repo: TaskResultsRepository = TaskResultsRepository(),
        checking_service: CheckingService | None = None,
    ) -> None:
        super().__init__(repo)
        self._checking = checking_service or CheckingService()

    async def create_from_check_result(
        self,
        db: AsyncSession,
        *,
        attempt_id: int | None,
        task_id: int,
        user_id: int,
        answer: StudentAnswer,
        check_result: CheckResult,
        source_system: str = "system",
        metrics: Any | None = None,
        count_retry: int = 0,
    ) -> TaskResults:
        """
        Создать запись в task_results на основе результата проверки.

        :param db: Асинхронная сессия БД.
        :param attempt_id: ID попытки (может быть None для старых записей).
        :param task_id: ID задания.
        :param user_id: ID пользователя.
        :param answer: Исходный ответ ученика (StudentAnswer).
        :param check_result: Результат проверки (CheckResult).
        :param source_system: Источник системы.
        :param metrics: Доп. метрики (опционально).
        :param count_retry: Номер попытки/кол-во попыток.
        """
        obj_in = TaskResultCreate(
            score=check_result.score,
            user_id=user_id,
            task_id=task_id,
            metrics=metrics,
            count_retry=count_retry,
            attempt_id=attempt_id,
            answer_json=answer.model_dump(),
            max_score=check_result.max_score,
            is_correct=check_result.is_correct,
            source_system=source_system,
        )

        # BaseService.create ожидает dict[str, Any], поэтому передаём model_dump()
        return await self.create(db, obj_in.model_dump())

    async def get_by_user(
        self,
        db: AsyncSession,
        user_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list, int]:
        """
        Получить результаты пользователя с пагинацией.

        Args:
            db: Асинхронная сессия БД.
            user_id: ID пользователя.
            limit: Максимум записей на странице.
            offset: Смещение.

        Returns:
            Кортеж (список результатов, общее количество).
        """
        from sqlalchemy import desc

        filters = [self.repo.model.user_id == user_id]

        return await self.paginate(
            db,
            limit=limit,
            offset=offset,
            filters=filters,
            order_by=[desc(self.repo.model.submitted_at)],
        )

    async def get_by_task(
        self,
        db: AsyncSession,
        task_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list, int]:
        """
        Получить результаты по задаче с пагинацией.

        Args:
            db: Асинхронная сессия БД.
            task_id: ID задачи.
            limit: Максимум записей на странице.
            offset: Смещение.

        Returns:
            Кортеж (список результатов, общее количество).
        """
        from sqlalchemy import desc

        filters = [self.repo.model.task_id == task_id]

        return await self.paginate(
            db,
            limit=limit,
            offset=offset,
            filters=filters,
            order_by=[desc(self.repo.model.submitted_at)],
        )

    async def get_by_attempt(
        self,
        db: AsyncSession,
        attempt_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list, int]:
        """
        Получить результаты попытки с пагинацией.

        Args:
            db: Асинхронная сессия БД.
            attempt_id: ID попытки.
            limit: Максимум записей на странице.
            offset: Смещение.

        Returns:
            Кортеж (список результатов, общее количество).
        """
        from sqlalchemy import desc

        filters = [self.repo.model.attempt_id == attempt_id]

        return await self.paginate(
            db,
            limit=limit,
            offset=offset,
            filters=filters,
            order_by=[desc(self.repo.model.submitted_at)],
        )

    async def get_stats_by_task(
        self,
        db: AsyncSession,
        task_id: int,
    ) -> Dict[str, Any]:
        """Получить статистику по задаче."""
        from sqlalchemy import select
        
        total_query = select(func.count(TaskResults.id)).where(TaskResults.task_id == task_id)
        total_result = await db.execute(total_query)
        total_attempts = total_result.scalar() or 0
        
        if total_attempts == 0:
            return {
                "task_id": task_id,
                "total_attempts": 0,
                "average_score": 0.0,
                "correct_percentage": 0.0,
                "min_score": 0,
                "max_score": 0,
                "score_distribution": {},
            }
        
        stats_query = select(
            func.avg(TaskResults.score).label("avg_score"),
            func.sum(case((TaskResults.is_correct == True, 1), else_=0)).label("correct_count"),
            func.min(TaskResults.score).label("min_score"),
            func.max(TaskResults.score).label("max_score"),
        ).where(TaskResults.task_id == task_id)
        
        stats_result = await db.execute(stats_query)
        stats_row = stats_result.first()
        
        average_score = float(stats_row.avg_score or 0)
        correct_count = stats_row.correct_count or 0
        correct_percentage = (correct_count / total_attempts * 100) if total_attempts > 0 else 0.0
        
        return {
            "task_id": task_id,
            "total_attempts": total_attempts,
            "average_score": round(average_score, 2),
            "correct_percentage": round(correct_percentage, 2),
            "min_score": stats_row.min_score or 0,
            "max_score": stats_row.max_score or 0,
            "score_distribution": {},
        }

    async def get_stats_by_course(
        self,
        db: AsyncSession,
        course_id: int,
    ) -> Dict[str, Any]:
        """Получить статистику по курсу."""
        from sqlalchemy import select
        from app.models.tasks import Tasks
        
        tasks_query = select(Tasks.id).where(Tasks.course_id == course_id)
        tasks_result = await db.execute(tasks_query)
        task_ids = [row[0] for row in tasks_result]
        
        if not task_ids:
            return {
                "course_id": course_id,
                "total_attempts": 0,
                "average_score": 0.0,
                "correct_percentage": 0.0,
                "tasks_count": 0,
            }
        
        stats_query = select(
            func.count(TaskResults.id).label("total_attempts"),
            func.avg(TaskResults.score).label("avg_score"),
            func.sum(case((TaskResults.is_correct == True, 1), else_=0)).label("correct_count"),
        ).where(TaskResults.task_id.in_(task_ids))
        
        stats_result = await db.execute(stats_query)
        stats_row = stats_result.first()
        
        total_attempts = stats_row.total_attempts or 0
        if total_attempts == 0:
            return {
                "course_id": course_id,
                "total_attempts": 0,
                "average_score": 0.0,
                "correct_percentage": 0.0,
                "tasks_count": len(task_ids),
            }
        
        average_score = float(stats_row.avg_score or 0)
        correct_count = stats_row.correct_count or 0
        correct_percentage = (correct_count / total_attempts * 100) if total_attempts > 0 else 0.0
        
        return {
            "course_id": course_id,
            "total_attempts": total_attempts,
            "average_score": round(average_score, 2),
            "correct_percentage": round(correct_percentage, 2),
            "tasks_count": len(task_ids),
        }

    async def get_stats_by_user(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> Dict[str, Any]:
        """Получить статистику по пользователю."""
        from sqlalchemy import select
        
        stats_query = select(
            func.count(TaskResults.id).label("total_attempts"),
            func.avg(TaskResults.score).label("avg_score"),
            func.sum(case((TaskResults.is_correct == True, 1), else_=0)).label("correct_count"),
            func.sum(TaskResults.max_score).label("total_max_score"),
            func.sum(TaskResults.score).label("total_score"),
        ).where(TaskResults.user_id == user_id)
        
        stats_result = await db.execute(stats_query)
        stats_row = stats_result.first()
        
        total_attempts = stats_row.total_attempts or 0
        if total_attempts == 0:
            return {
                "user_id": user_id,
                "total_attempts": 0,
                "average_score": 0.0,
                "correct_percentage": 0.0,
                "total_score": 0,
                "total_max_score": 0,
                "completion_percentage": 0.0,
            }
        
        average_score = float(stats_row.avg_score or 0)
        correct_count = stats_row.correct_count or 0
        correct_percentage = (correct_count / total_attempts * 100) if total_attempts > 0 else 0.0
        total_score = stats_row.total_score or 0
        total_max_score = stats_row.total_max_score or 0
        completion_percentage = (total_score / total_max_score * 100) if total_max_score > 0 else 0.0
        
        return {
            "user_id": user_id,
            "total_attempts": total_attempts,
            "average_score": round(average_score, 2),
            "correct_percentage": round(correct_percentage, 2),
            "total_score": total_score,
            "total_max_score": total_max_score,
            "completion_percentage": round(completion_percentage, 2),
        }