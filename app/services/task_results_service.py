from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

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
