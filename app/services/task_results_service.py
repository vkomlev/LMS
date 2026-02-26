from typing import Any, Dict, List, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, case, text

from app.models.attempts import Attempts
from app.models.task_results import TaskResults
from app.repos.task_results_repo import TaskResultsRepository
from app.schemas.checking import StudentAnswer, CheckResult
from app.schemas.task_results import TaskResultCreate
from app.services.base import BaseService
from app.services.checking_service import CheckingService

# Learning Engine V1, этап 6: порог для PASS по последней попытке
PASS_THRESHOLD_RATIO = 0.5


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

    async def _last_attempts_flat(
        self,
        db: AsyncSession,
        *,
        user_id: int | None = None,
        task_id: int | None = None,
        task_ids: List[int] | None = None,
    ) -> List[Tuple[int, int, int, int]]:
        """
        Последняя завершённая попытка по каждой паре (user_id, task_id).
        Возвращает список (user_id, task_id, score, max_score). max_score может быть 0.
        """
        conditions = ["a.finished_at IS NOT NULL", "a.cancelled_at IS NULL"]
        params: Dict[str, Any] = {}
        if user_id is not None:
            conditions.append("tr.user_id = :user_id")
            params["user_id"] = user_id
        if task_id is not None:
            conditions.append("tr.task_id = :task_id")
            params["task_id"] = task_id
        if task_ids is not None:
            conditions.append("tr.task_id = ANY(:task_ids)")
            params["task_ids"] = task_ids
        where_sql = " AND ".join(conditions)
        stmt = text(f"""
            WITH ranked AS (
                SELECT tr.user_id, tr.task_id, tr.score,
                    COALESCE(tr.max_score, 0) AS max_score,
                    ROW_NUMBER() OVER (
                        PARTITION BY tr.user_id, tr.task_id
                        ORDER BY a.finished_at DESC, a.id DESC
                    ) AS rn
                FROM task_results tr
                INNER JOIN attempts a ON a.id = tr.attempt_id
                WHERE {where_sql}
            )
            SELECT user_id, task_id, score, max_score FROM ranked WHERE rn = 1
        """)
        r = await db.execute(stmt, params)
        return [(row[0], row[1], int(row[2]), int(row[3])) for row in r.fetchall()]

    @staticmethod
    def _is_pass(score: int, max_score: int) -> bool:
        return max_score > 0 and (score / max_score) >= PASS_THRESHOLD_RATIO

    async def get_stats_by_task(
        self,
        db: AsyncSession,
        task_id: int,
    ) -> Dict[str, Any]:
        """
        Статистика по задаче. Основные показатели — по last-attempt (этап 6);
        average_score, min_score, max_score — дополнительные (по всем попыткам).
        """
        from sqlalchemy import select

        last_rows = await self._last_attempts_flat(db, task_id=task_id)
        last_passed = sum(1 for _, _, s, m in last_rows if self._is_pass(s, m))
        last_failed = len(last_rows) - last_passed
        total_with_last = len(last_rows)

        total_query = (
            select(func.count(TaskResults.id))
            .select_from(TaskResults)
            .join(Attempts, TaskResults.attempt_id == Attempts.id)
            .where(TaskResults.task_id == task_id, Attempts.cancelled_at.is_(None))
        )
        total_result = await db.execute(total_query)
        total_attempts = total_result.scalar() or 0
        progress_percent = (last_passed / total_with_last * 100) if total_with_last > 0 else 0.0

        if total_attempts == 0:
            return {
                "task_id": task_id,
                "total_attempts": 0,
                "average_score": 0.0,
                "correct_percentage": 0.0,
                "min_score": 0,
                "max_score": 0,
                "score_distribution": {},
                "progress_percent": round(progress_percent, 2),
                "passed_tasks_count": last_passed,
                "failed_tasks_count": last_failed,
                "last_passed_count": last_passed,
                "last_failed_count": last_failed,
            }

        stats_query = (
            select(
                func.avg(TaskResults.score).label("avg_score"),
                func.sum(case((TaskResults.is_correct == True, 1), else_=0)).label("correct_count"),
                func.min(TaskResults.score).label("min_score"),
                func.max(TaskResults.score).label("max_score"),
            )
            .select_from(TaskResults)
            .join(Attempts, TaskResults.attempt_id == Attempts.id)
            .where(TaskResults.task_id == task_id, Attempts.cancelled_at.is_(None))
        )
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
            "progress_percent": round(progress_percent, 2),
            "passed_tasks_count": last_passed,
            "failed_tasks_count": last_failed,
            "last_passed_count": last_passed,
            "last_failed_count": last_failed,
        }

    async def get_stats_by_course(
        self,
        db: AsyncSession,
        course_id: int,
    ) -> Dict[str, Any]:
        """
        Статистика по курсу. Основные показатели — по last-attempt (этап 6);
        average_score, total_attempts — дополнительные.
        """
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
                "progress_percent": 0.0,
                "passed_tasks_count": 0,
                "failed_tasks_count": 0,
            }

        last_rows = await self._last_attempts_flat(db, task_ids=task_ids)
        last_passed = sum(1 for _, _, s, m in last_rows if self._is_pass(s, m))
        last_failed = len(last_rows) - last_passed
        total_with_last = len(last_rows)
        progress_percent = (last_passed / total_with_last * 100) if total_with_last > 0 else 0.0

        stats_query = (
            select(
                func.count(TaskResults.id).label("total_attempts"),
                func.avg(TaskResults.score).label("avg_score"),
                func.sum(case((TaskResults.is_correct == True, 1), else_=0)).label("correct_count"),
            )
            .select_from(TaskResults)
            .join(Attempts, TaskResults.attempt_id == Attempts.id)
            .where(
                TaskResults.task_id.in_(task_ids),
                Attempts.cancelled_at.is_(None),
            )
        )
        stats_result = await db.execute(stats_query)
        stats_row = stats_result.first()

        total_attempts = stats_row.total_attempts or 0
        average_score = float(stats_row.avg_score or 0)
        correct_count = stats_row.correct_count or 0
        correct_percentage = (correct_count / total_attempts * 100) if total_attempts > 0 else 0.0

        return {
            "course_id": course_id,
            "total_attempts": total_attempts,
            "average_score": round(average_score, 2),
            "correct_percentage": round(correct_percentage, 2),
            "tasks_count": len(task_ids),
            "progress_percent": round(progress_percent, 2),
            "passed_tasks_count": last_passed,
            "failed_tasks_count": last_failed,
        }

    async def get_stats_by_user(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> Dict[str, Any]:
        """
        Статистика по пользователю. Основной статус и прогресс — по last-attempt (этап 6);
        average_score, total_score, total_max_score — дополнительные (по всем попыткам).
        """
        from sqlalchemy import select

        last_rows = await self._last_attempts_flat(db, user_id=user_id)
        last_passed = sum(1 for _, _, s, m in last_rows if self._is_pass(s, m))
        last_failed = len(last_rows) - last_passed
        total_with_last = len(last_rows)
        progress_percent = (last_passed / total_with_last * 100) if total_with_last > 0 else 0.0
        last_score = sum(row[2] for row in last_rows)
        last_max_score = sum(row[3] for row in last_rows)
        last_ratio = (last_score / last_max_score) if last_max_score and last_max_score > 0 else 0.0

        stats_query = (
            select(
                func.count(TaskResults.id).label("total_attempts"),
                func.avg(TaskResults.score).label("avg_score"),
                func.sum(case((TaskResults.is_correct == True, 1), else_=0)).label("correct_count"),
                func.sum(TaskResults.max_score).label("total_max_score"),
                func.sum(TaskResults.score).label("total_score"),
            )
            .select_from(TaskResults)
            .join(Attempts, TaskResults.attempt_id == Attempts.id)
            .where(TaskResults.user_id == user_id, Attempts.cancelled_at.is_(None))
        )
        stats_result = await db.execute(stats_query)
        stats_row = stats_result.first()

        total_attempts = stats_row.total_attempts or 0
        if total_attempts == 0 and total_with_last == 0:
            return {
                "user_id": user_id,
                "total_attempts": 0,
                "average_score": 0.0,
                "correct_percentage": 0.0,
                "total_score": 0,
                "total_max_score": 0,
                "completion_percentage": 0.0,
                "progress_percent": 0.0,
                "passed_tasks_count": 0,
                "failed_tasks_count": 0,
                "current_score": 0,
                "current_ratio": 0.0,
                "last_score": 0,
                "last_max_score": 0,
                "last_ratio": 0.0,
            }

        average_score = float(stats_row.avg_score or 0)
        correct_count = stats_row.correct_count or 0
        correct_percentage = (correct_count / total_attempts * 100) if total_attempts > 0 else 0.0
        total_score = stats_row.total_score or 0
        total_max_score = stats_row.total_max_score or 0
        completion_percentage = (total_score / total_max_score * 100) if total_max_score and total_max_score > 0 else 0.0

        return {
            "user_id": user_id,
            "total_attempts": total_attempts,
            "average_score": round(average_score, 2),
            "correct_percentage": round(correct_percentage, 2),
            "total_score": total_score,
            "total_max_score": total_max_score,
            "completion_percentage": round(completion_percentage, 2),
            "progress_percent": round(progress_percent, 2),
            "passed_tasks_count": last_passed,
            "failed_tasks_count": last_failed,
            "current_score": last_score,
            "current_ratio": round(last_ratio, 4),
            "last_score": last_score,
            "last_max_score": last_max_score,
            "last_ratio": round(last_ratio, 4),
        }