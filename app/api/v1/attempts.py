from __future__ import annotations

from typing import List, Optional
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Body, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_db
from app.models.attempts import Attempts
from app.models.task_results import TaskResults

from app.schemas.attempts import (
    AttemptCreate,
    AttemptRead,
    AttemptWithResults,
    AttemptTaskResultShort,
    AttemptAnswersRequest,
    AttemptAnswersResponse,
    AttemptAnswerResult,
    AttemptFinishResponse,
)
from app.schemas.checking import (
    StudentAnswer,
    CheckResult,
)
from app.schemas.task_content import TaskContent
from app.schemas.solution_rules import SolutionRules

from app.services.attempts_service import AttemptsService
from app.services.task_results_service import TaskResultsService
from app.services.tasks_service import TasksService
from app.services.checking_service import CheckingService

from app.utils.exceptions import DomainError


router = APIRouter(tags=["attempts"])

attempts_service = AttemptsService()
task_results_service = TaskResultsService()
tasks_service = TasksService()
checking_service = CheckingService()


# ---------- Внутренний helper для сборки AttemptWithResults ----------


async def _build_attempt_with_results(
    db: AsyncSession,
    attempt: Attempts,
) -> AttemptWithResults:
    """
    Собрать AttemptWithResults по объекту Attempts и строкам task_results.

    Здесь специально не выносим в сервис, чтобы минимально трогать доменную логику,
    как ты просил — «нет только самих эндпойнтов».
    """
    stmt = select(TaskResults).where(TaskResults.attempt_id == attempt.id)
    result = await db.execute(stmt)
    rows: List[TaskResults] = result.scalars().all()

    results_short: List[AttemptTaskResultShort] = []
    total_score = 0
    total_max_score = 0

    for row in rows:
        score = row.score or 0
        max_score = row.max_score or 0

        results_short.append(
            AttemptTaskResultShort(
                task_id=row.task_id,
                score=score,
                max_score=max_score,
                is_correct=row.is_correct,
                answer_json=row.answer_json,
            )
        )
        total_score += score
        total_max_score += max_score

    attempt_read = AttemptRead.model_validate(attempt)

    return AttemptWithResults(
        attempt=attempt_read,
        results=results_short,
        total_score=total_score,
        total_max_score=total_max_score,
    )


# ---------- Эндпойнты ----------


@router.post(
    "/attempts",
    response_model=AttemptRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать попытку прохождения теста/набора задач",
)
async def create_attempt(
    payload: AttemptCreate = Body(
        ...,
        description="Параметры новой попытки (user_id, course_id, source_system, meta).",
    ),
    db: AsyncSession = Depends(get_db),
) -> AttemptRead:
    """
    Создать новую попытку.

    Используется существующий AttemptsService.create_attempt.
    """
    attempt = await attempts_service.create_attempt(
        db=db,
        user_id=payload.user_id,
        course_id=payload.course_id,
        source_system=payload.source_system,
        meta=payload.meta,
    )
    # BaseService возвращает ORM-модель → Pydantic сам соберёт по from_attributes
    return AttemptRead.model_validate(attempt)


@router.post(
    "/attempts/{attempt_id}/answers",
    response_model=AttemptAnswersResponse,
    summary="Отправить ответы по задачам внутри попытки",
)
async def submit_attempt_answers(
    attempt_id: int,
    payload: AttemptAnswersRequest = Body(
        ...,
        description="Список ответов ученика по задачам в рамках попытки.",
    ),
    db: AsyncSession = Depends(get_db),
) -> AttemptAnswersResponse:
    """
    Принять ответы по задачам в рамках попытки, проверить их и записать в task_results.

    Логика:
    1. Находим попытку.
    2. Для каждого ответа:
       - определяем задачу (по task_id или external_uid),
       - приводим task_content / solution_rules к схемам,
       - вызываем CheckingService,
       - создаём запись в task_results через TaskResultsService.create_from_check_result.
    3. Суммируем набранные и максимальные баллы по этим ответам.
    """
    # 1. Находим попытку
    try:
        attempt = await attempts_service.get_by_id(db, attempt_id)
    except DomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    # Валидация попытки: проверка, что попытка не завершена
    if attempt.finished_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Попытка уже завершена. Нельзя отправлять ответы в завершенную попытку.",
        )

    # Валидация попытки: проверка таймлимита (если указан в meta)
    if attempt.meta and isinstance(attempt.meta, dict) and "time_limit" in attempt.meta:
        time_limit_seconds = attempt.meta.get("time_limit")
        if time_limit_seconds and isinstance(time_limit_seconds, (int, float)):
            elapsed = datetime.now(timezone.utc) - attempt.created_at
            if elapsed > timedelta(seconds=time_limit_seconds):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Время на выполнение истекло.",
                )

    if not payload.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Список ответов не может быть пустым.",
        )

    results: List[AttemptAnswerResult] = []
    total_score_delta = 0
    total_max_score_delta = 0

    for item in payload.items:
        # 2.1 Определяем задачу
        task = None
        if item.task_id is not None:
            task = await tasks_service.get_by_id(db, item.task_id)
        elif item.external_uid:
            task = await tasks_service.get_by_external_uid(db, item.external_uid)

        if task is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Задача для ответа не найдена "
                    f"(task_id={item.task_id}, external_uid={item.external_uid!r})."
                ),
            )

        # 2.2 Приводим JSON к строгим схемам
        task_content = TaskContent.model_validate(task.task_content)
        solution_rules = SolutionRules.model_validate(task.solution_rules or {})

        # 2.3 Проверяем ответ
        answer: StudentAnswer = item.answer
        if answer.type != task_content.type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Тип ответа ({answer.type}) не совпадает с типом задачи "
                    f"({task_content.type})."
                ),
            )

        check_result: CheckResult = checking_service.check_task(
            task_content=task_content,
            solution_rules=solution_rules,
            answer=answer,
        )

        # 2.4 Записываем в task_results
        await task_results_service.create_from_check_result(
            db=db,
            attempt_id=attempt.id,
            task_id=task.id,
            user_id=attempt.user_id,
            answer=answer,
            check_result=check_result,
            source_system=attempt.source_system,
        )

        # 2.5 Накопление для ответа
        results.append(
            AttemptAnswerResult(
                task_id=task.id,
                check_result=check_result,
            )
        )
        total_score_delta += check_result.score
        total_max_score_delta += check_result.max_score

    return AttemptAnswersResponse(
        attempt_id=attempt.id,
        results=results,
        total_score_delta=total_score_delta,
        total_max_score_delta=total_max_score_delta,
    )


@router.post(
    "/attempts/{attempt_id}/finish",
    response_model=AttemptFinishResponse,
    summary="Завершить попытку и вернуть агрегированные результаты",
)
async def finish_attempt(
    attempt_id: int,
    db: AsyncSession = Depends(get_db),
) -> AttemptFinishResponse:
    """
    Завершить попытку:

    1. Проставить finished_at через AttemptsService.finish_attempt.
    2. Собрать AttemptWithResults (все task_results по попытке, суммы баллов).
    """
    try:
        attempt = await attempts_service.finish_attempt(db, attempt_id)
    except DomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    attempt_with_results = await _build_attempt_with_results(db, attempt)

    # ⬇️ ключевая правка
    return AttemptFinishResponse.model_validate(
        attempt_with_results.model_dump()
    )


@router.get(
    "/attempts/{attempt_id}",
    response_model=AttemptWithResults,
    summary="Получить попытку с результатами по задачам",
)
async def get_attempt(
    attempt_id: int,
    db: AsyncSession = Depends(get_db),
) -> AttemptWithResults:
    """
    Вернуть попытку и все результаты по задачам:

    - метаданные попытки,
    - список task_results в свернутом виде (AttemptTaskResultShort),
    - total_score и total_max_score.
    """
    try:
        attempt = await attempts_service.get_by_id(db, attempt_id)
    except DomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    attempt_with_results = await _build_attempt_with_results(db, attempt)
    return attempt_with_results


@router.get(
    "/attempts/by-user/{user_id}",
    response_model=List[AttemptRead],
    summary="Получить попытки пользователя",
)
async def get_attempts_by_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    course_id: Optional[int] = Query(None, description="Фильтр по курсу"),
    limit: int = Query(100, ge=1, le=1000, description="Максимум записей на странице"),
    offset: int = Query(0, ge=0, description="Смещение"),
) -> List[AttemptRead]:
    """
    Получить список попыток пользователя с пагинацией.

    Поддерживается опциональная фильтрация по курсу.
    Результаты сортируются по дате создания (от новых к старым).

    Args:
        user_id: ID пользователя.
        course_id: Опциональный фильтр по курсу.
        limit: Максимум записей на странице (1-1000).
        offset: Смещение для пагинации.

    Returns:
        Список попыток пользователя.
    """
    attempts, total = await attempts_service.get_by_user(
        db,
        user_id=user_id,
        course_id=course_id,
        limit=limit,
        offset=offset,
    )
    return [AttemptRead.model_validate(attempt) for attempt in attempts]
