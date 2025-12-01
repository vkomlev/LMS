from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.checking import (
    SingleCheckRequest,
    BatchCheckRequest,
    BatchCheckResponse,
    BatchCheckItemResult,
    CheckResult,
)
from app.services.checking_service import CheckingService

router = APIRouter(
    prefix="/api/v1/check",
    tags=["checking"],
)

checking_service = CheckingService()


@router.post(
    "/task",
    response_model=CheckResult,
    summary="Stateless-проверка одной задачи",
    description=(
        "Проверяет один ответ ученика по переданным JSON-описаниям "
        "task_content и solution_rules, без обращения к БД."
    ),
)
async def check_task(payload: SingleCheckRequest) -> CheckResult:
    """
    Stateless-проверка одной задачи.

    Принимает:
    - JSON описания задания (task_content);
    - JSON правил проверки (solution_rules);
    - ответ ученика (answer).

    Возвращает:
    - результат проверки (CheckResult).
    """
    try:
        return checking_service.check_answer(
            task_content=payload.task_content,
            rules=payload.solution_rules,
            answer=payload.answer,
        )
    except ValueError as exc:
        # Логические ошибки (несовпадение типов, некорректная структура ответа и т.п.)
        # отдаём как 400 Bad Request.
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/tasks-batch",
    response_model=BatchCheckResponse,
    summary="Stateless-проверка нескольких задач",
    description=(
        "Выполняет пакетную проверку нескольких задач по переданным JSON-описаниям "
        "task_content и solution_rules, без обращения к БД."
    ),
)
async def check_tasks_batch(payload: BatchCheckRequest) -> BatchCheckResponse:
    """
    Stateless-проверка нескольких задач (batch).

    Принимает массив объектов TaskWithAnswer.
    На каждый элемент возвращает CheckResult вместе с индексом.
    """
    results: list[BatchCheckItemResult] = []

    for index, item in enumerate(payload.items):
        try:
            check_result = checking_service.check_answer(
                task_content=item.task_content,
                rules=item.solution_rules,
                answer=item.answer,
            )
        except ValueError as exc:
            # Вариант 1 (простой): полностью проваливаем запрос с 400 и указанием индекса.
            raise HTTPException(
                status_code=400,
                detail=f"Ошибка при проверке элемента с индексом {index}: {exc}",
            )

        results.append(
            BatchCheckItemResult(
                index=index,
                result=check_result,
            )
        )

    return BatchCheckResponse(results=results)
