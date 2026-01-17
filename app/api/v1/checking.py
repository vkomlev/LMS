# app/api/v1/checking.py

from __future__ import annotations

import logging

from fastapi import APIRouter
from app.schemas.checking import (
    SingleCheckRequest,
    CheckResult,
    BatchCheckRequest,
    BatchCheckResponse,
    BatchCheckItemResult,
)
from app.services.checking_service import CheckingService
from app.utils.exceptions import DomainError

logger = logging.getLogger("api.checking")

router = APIRouter(
    prefix="/check",
    tags=["checking"],
)

checking_service = CheckingService()


@router.post(
    "/task",
    response_model=CheckResult,
    summary="Проверка одной задачи",
    responses={
        200: {
            "description": "Проверка выполнена успешно",
            "content": {
                "application/json": {
                    "example": {
                        "score": 10,
                        "max_score": 10,
                        "is_correct": True,
                        "feedback": [
                            {
                                "type": "correct",
                                "message": "Правильно! Переменная действительно хранит данные в памяти.",
                            }
                        ],
                    }
                }
            }
        },
        400: {
            "description": "Ошибка валидации данных задачи или ответа",
            "content": {
                "application/json": {
                    "example": {
                        "error": "domain_error",
                        "detail": "Неверный тип ответа для задачи типа SC",
                    }
                }
            }
        },
        422: {
            "description": "Ошибка валидации запроса (неверный формат JSON)",
        },
    },
)
async def check_task_endpoint(payload: SingleCheckRequest) -> CheckResult:
    """
    Stateless-проверка **одной** задачи.

    На вход принимает:
    - task_content: JSON-описание задания;
    - solution_rules: JSON-правила проверки;
    - answer: ответ ученика.

    На выходе — CheckResult без сохранения в БД.
    """
    logger.info("check_task: type=%s", payload.answer.type)
    try:
        result = checking_service.check_task(
            task_content=payload.task_content,
            solution_rules=payload.solution_rules,
            answer=payload.answer,
        )
        logger.debug(
            "check_task: score=%s/%s",
            result.score,
            result.max_score,
        )
        return result
    except DomainError:
        # DomainError перехватывается глобальным хэндлером в app/api/main.py
        raise
    except Exception as exc:  # на случай непредвиденной ошибки
        logger.exception("check_task: unexpected error: %s", exc)
        # Позволяем глобальному 500-хэндлеру отработать
        raise


@router.post(
    "/tasks-batch",
    response_model=BatchCheckResponse,
    summary="Проверка набора задач",
    responses={
        200: {
            "description": "Проверка выполнена успешно",
            "content": {
                "application/json": {
                    "example": {
                        "results": [
                            {
                                "index": 0,
                                "result": {
                                    "score": 10,
                                    "max_score": 10,
                                    "is_correct": True,
                                    "feedback": [],
                                },
                            },
                            {
                                "index": 1,
                                "result": {
                                    "score": 0,
                                    "max_score": 10,
                                    "is_correct": False,
                                    "feedback": [],
                                },
                            },
                        ]
                    }
                }
            }
        },
        400: {
            "description": "Ошибка валидации данных задач или ответов",
        },
        422: {
            "description": "Ошибка валидации запроса (неверный формат JSON)",
        },
    },
)
async def check_tasks_batch_endpoint(
    payload: BatchCheckRequest,
) -> BatchCheckResponse:
    """
    Stateless-проверка **набора** задач.

    Для каждого элемента массива items возвращается:
    - index: индекс элемента во входном списке;
    - result: CheckResult.
    """
    logger.info("check_tasks_batch: items=%d", len(payload.items))
    results: list[BatchCheckItemResult] = []

    for index, item in enumerate(payload.items):
        try:
            result = checking_service.check_task(
                task_content=item.task_content,
                solution_rules=item.solution_rules,
                answer=item.answer,
            )
            results.append(
                BatchCheckItemResult(
                    index=index,
                    result=result,
                )
            )
        except DomainError:
            # DomainError для батча пробрасываем наружу —
            # его перехватит глобальный обработчик, как и для одиночного вызова.
            logger.warning(
                "check_tasks_batch: domain error at index=%d",
                index,
            )
            raise
        except Exception as exc:
            logger.exception(
                "check_tasks_batch: unexpected error at index=%d: %s",
                index,
                exc,
            )
            raise

    return BatchCheckResponse(results=results)
