# app/api/v1/checking.py

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, get_current_user, require_role
from app.auth.current_user import CurrentUser
from app.schemas.task_content import TaskContent
from app.services.code_review_service import pick_program_for_io_tests
from app.services.task_form_flags import compute_task_form_flags
from app.services.tasks_service import TasksService
from app.schemas.checking import (
    StudentAnswer,
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
tasks_service = TasksService()

# tsk-1146: предпросмотр задания «глазами ученика» — только staff.
_PREVIEW_GATE = require_role("teacher", "methodist", "admin")


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
        401: {"description": "Не аутентифицирован"},
        422: {
            "description": "Ошибка валидации запроса (неверный формат JSON)",
        },
    },
)
async def check_task_endpoint(
    payload: SingleCheckRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> CheckResult:
    """
    Stateless-проверка **одной** задачи.

    На вход принимает:
    - task_content: JSON-описание задания;
    - solution_rules: JSON-правила проверки;
    - answer: ответ ученика.

    На выходе — CheckResult без сохранения в БД.

    Доступ: любой аутентифицированный пользователь (cookie ИЛИ сервисный ключ) —
    tsk-461, до этого эндпоинт был открыт без единого гейта.
    """
    logger.info(
        "check_task: type=%s, scoring_mode=%s, has_custom_config=%s",
        payload.answer.type,
        payload.solution_rules.scoring_mode,
        payload.solution_rules.custom_scoring_config is not None,
    )
    try:
        result = await asyncio.to_thread(
            checking_service.check_task,
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
    except DomainError as e:
        # DomainError перехватывается глобальным хэндлером в app/api/main.py
        logger.warning("check_task: DomainError: %s", e.detail)
        raise
    except Exception as exc:  # на случай непредвиденной ошибки
        logger.exception("check_task: unexpected error: %s", exc)
        # Позволяем глобальному 500-хэндлеру отработать
        raise


class TaskPreviewFlags(BaseModel):
    """Флаги формы ответа для предпросмотра — те же, что в состоянии задания ученика."""

    task_id: int
    requires_attachment: bool
    partial_auto_check: bool
    has_reference_answer: bool
    has_io_tests: bool


@router.get(
    "/tasks/{task_id}/preview",
    response_model=TaskPreviewFlags,
    summary="Предпросмотр задания преподавателем: флаги формы без записи (tsk-1146)",
)
async def preview_task_flags_endpoint(
    task_id: int,
    current_user: CurrentUser = Depends(_PREVIEW_GATE),
    db: AsyncSession = Depends(get_bare_db),
) -> TaskPreviewFlags:
    """
    Флаги формы ответа для предпросмотра.

    Замена ученическому `GET /learning/tasks/{id}/state`, который считает попытки
    вызывающего и при исчерпанном лимите создаёт заявку помощи. Здесь — только
    чтение задания, без попыток и без записи.
    """
    task = await tasks_service.get_by_id(db, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задание не найдено")
    flags = compute_task_form_flags(task.solution_rules, task.task_content)
    return TaskPreviewFlags(
        task_id=task.id,
        requires_attachment=flags.requires_attachment,
        partial_auto_check=flags.partial_auto_check,
        has_reference_answer=flags.has_reference_answer,
        has_io_tests=flags.has_io_tests,
    )


@router.post(
    "/tasks/{task_id}/preview",
    response_model=CheckResult,
    summary="Предпросмотр задания преподавателем: проверка ответа без записи (tsk-1146)",
    responses={
        400: {"description": "Тип ответа не совпадает с типом задания"},
        403: {"description": "Только преподаватель, методист или администратор"},
        404: {"description": "Задание не найдено"},
    },
)
async def preview_check_task_endpoint(
    task_id: int,
    answer: StudentAnswer = Body(..., description="Ответ в том же виде, что сдаёт ученик."),
    current_user: CurrentUser = Depends(_PREVIEW_GATE),
    db: AsyncSession = Depends(get_bare_db),
) -> CheckResult:
    """
    Проверить ответ на задание движком ученика, НИЧЕГО не записывая.

    Режим «глазами ученика» для staff (tsk-1146): преподаватель открывает задание
    чистым и пробует ответить. Обычная сдача пишет attempts, task_results,
    learning_events (task_opened), явку, заявки помощи — здесь нет ни одного из
    этих вызовов: задание читается, ответ проверяется stateless-движком
    (`CheckingService.check_task`), сессия БД не коммитится. Для тестов
    ввода/вывода программа берётся из текста ответа, вложения не читаются
    (у предпросмотра нет попытки, к которой они привязаны).

    Доступ — только teacher/methodist/admin: результат проверки может раскрыть
    эталон, ученику этот путь закрыт (403).
    """
    task = await tasks_service.get_by_id(db, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задание не найдено")

    try:
        task_content = TaskContent.model_validate(task.task_content)
        solution_rules = checking_service.build_solution_rules(
            task.solution_rules, task.max_score
        )
    except ValidationError as exc:
        logger.warning("preview_check: битое задание task_id=%s: %s", task.id, exc)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Задание сохранено с ошибкой и не проверяется."
        ) from exc
    if answer.type != task_content.type:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Тип ответа ({answer.type}) не совпадает с типом задачи ({task_content.type}).",
        )

    check_answer = answer
    if solution_rules.io_tests is not None:
        picked_program = pick_program_for_io_tests(
            answer.response.value,
            answer.response.comment,
            None,
            attempt_id=None,
            task_id=task.id,
        )
        check_answer = answer.model_copy(deep=True)
        check_answer.response.value = picked_program or ""

    logger.info(
        "preview_check: user_id=%s task_id=%s type=%s",
        current_user.id, task.id, task_content.type,
    )
    return await asyncio.to_thread(
        checking_service.check_task,
        task_content=task_content,
        solution_rules=solution_rules,
        answer=check_answer,
    )


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
        401: {"description": "Не аутентифицирован"},
        422: {
            "description": "Ошибка валидации запроса (неверный формат JSON)",
        },
    },
)
async def check_tasks_batch_endpoint(
    payload: BatchCheckRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> BatchCheckResponse:
    """
    Stateless-проверка **набора** задач.

    Для каждого элемента массива items возвращается:
    - index: индекс элемента во входном списке;
    - result: CheckResult.

    Доступ: любой аутентифицированный пользователь (cookie ИЛИ сервисный ключ) —
    tsk-461, до этого эндпоинт был открыт без единого гейта.
    """
    logger.info("check_tasks_batch: items=%d", len(payload.items))
    results: list[BatchCheckItemResult] = []

    for index, item in enumerate(payload.items):
        try:
            result = await asyncio.to_thread(
                checking_service.check_task,
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
