from __future__ import annotations

from typing import List, Optional
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import mimetypes
import os
import re

from fastapi import APIRouter, Depends, Body, File, HTTPException, status, Query, UploadFile
from starlette.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_bare_db, get_current_user
from app.auth.current_user import CurrentUser
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
    AttemptAttachmentRead,
    AttemptFinishResponse,
    AttemptCancelRequest,
    AttemptCancelResponse,
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
from app.services.learning_engine_service import LearningEngineService
from app.core.config import Settings

from app.utils.exceptions import DomainError

import logging

router = APIRouter(tags=["attempts"])
logger = logging.getLogger("api.attempts")
settings = Settings()
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
ATTEMPT_ATTACHMENT_ID_RE = re.compile(r"^(?P<attempt_id>\d+)_[a-f0-9]{32}_[A-Za-z0-9._-]+$")

attempts_service = AttemptsService()
task_results_service = TaskResultsService()
tasks_service = TasksService()
checking_service = CheckingService()
learning_engine_service = LearningEngineService()


def _safe_upload_filename(filename: str | None) -> str:
    base = os.path.basename(filename or "attachment")
    safe = SAFE_FILENAME_RE.sub("_", base).strip("._")
    return safe or "attachment"


def _attempt_attachment_files(attempt_id: int) -> list[os.PathLike]:
    prefix = f"{attempt_id}_"
    upload_dir = settings.attempt_attachments_upload_dir
    if not upload_dir.exists():
        return []
    return sorted(path for path in upload_dir.iterdir() if path.is_file() and path.name.startswith(prefix))


def _validate_attempt_attachment_id(attempt_id: int, attachment_id: str) -> str:
    safe_id = _safe_upload_filename(attachment_id)
    match = ATTEMPT_ATTACHMENT_ID_RE.fullmatch(attachment_id)
    if safe_id != attachment_id or match is None or int(match.group("attempt_id")) != attempt_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    return safe_id


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


async def _enrich_attempt_with_learning_fields(
    db: AsyncSession,
    attempt_with_results: AttemptWithResults,
    attempt: Attempts,
) -> None:
    """
    Заполняет attempts_used, attempts_limit_effective, last_based_status
    по первой задаче в попытке (Learning Engine V1, этап 4).
    """
    if not attempt_with_results.results:
        return
    first_task_id = attempt_with_results.results[0].task_id
    state = await learning_engine_service.compute_task_state(db, attempt.user_id, first_task_id)
    attempt_with_results.attempts_used = state.attempts_used
    attempt_with_results.attempts_limit_effective = state.attempts_limit_effective
    attempt_with_results.last_based_status = state.state


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
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> AttemptRead:
    if not current_user.is_service and current_user.id != payload.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
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
    responses={
        200: {
            "description": "Ответы успешно отправлены и проверены",
            "content": {
                "application/json": {
                    "example": {
                        "attempt_id": 1,
                        "total_score": 25,
                        "max_score": 30,
                        "results": [
                            {
                                "task_id": 1,
                                "score": 10,
                                "max_score": 10,
                                "is_correct": True,
                            },
                            {
                                "task_id": 2,
                                "score": 15,
                                "max_score": 20,
                                "is_correct": False,
                            },
                        ],
                    }
                }
            }
        },
        400: {
            "description": "Попытка уже завершена или истекло время",
            "content": {
                "application/json": {
                    "examples": {
                        "finished": {
                            "summary": "Попытка завершена",
                            "value": {
                                "detail": "Попытка уже завершена. Нельзя отправлять ответы в завершенную попытку."
                            }
                        },
                        "timeout": {
                            "summary": "Истекло время",
                            "value": {
                                "detail": "Время на выполнение истекло"
                            }
                        },
                    }
                }
            }
        },
        404: {
            "description": "Попытка не найдена",
        },
        422: {
            "description": "Ошибка валидации запроса (неверный формат JSON)",
        },
    },
)
async def submit_attempt_answers(
    attempt_id: int,
    payload: AttemptAnswersRequest = Body(
        ...,
        description="Список ответов ученика по задачам в рамках попытки.",
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
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

    if not current_user.is_service and current_user.id != attempt.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")

    # Валидация попытки: проверка, что попытка не завершена и не отменена
    if attempt.finished_at is not None:
        logger.warning(
            "POST /attempts/%s/answers: попытка уже завершена",
            attempt_id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Попытка уже завершена. Нельзя отправлять ответы в завершенную попытку.",
        )
    if attempt.cancelled_at is not None:
        logger.warning(
            "POST /attempts/%s/answers: попытка отменена",
            attempt_id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Попытка отменена. Нельзя отправлять ответы в отменённую попытку.",
        )

    # Таймлимит проверяется по каждой задаче (tasks.time_limit_sec) ниже; при просрочке
    # попытка помечается time_expired=true и по просроченным заданиям пишется score=0.

    if not payload.items:
        logger.warning(
            "POST /attempts/%s/answers: пустой список ответов",
            attempt_id,
        )
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
            logger.warning(
                "POST /attempts/%s/answers: задача не найдена (task_id=%s, external_uid=%r), answer.type=%s",
                attempt_id,
                item.task_id,
                item.external_uid,
                getattr(item.answer, "type", None),
            )
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
            logger.warning(
                "POST /attempts/%s/answers: несовпадение типа ответа с типом задачи (answer.type=%s, task.type=%s)",
                attempt_id,
                answer.type,
                task_content.type,
            )
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

        # 2.3b Learning Engine V1: таймлимит из tasks.time_limit_sec; при просрочке score=0
        now = datetime.now(timezone.utc)
        task_deadline_sec = getattr(task, "time_limit_sec", None) or (
            attempt.meta.get("time_limit") if isinstance(attempt.meta, dict) else None
        )
        if attempt.time_expired:
            check_result = CheckResult(score=0, max_score=check_result.max_score, is_correct=False)
        elif task_deadline_sec and isinstance(task_deadline_sec, (int, float)):
            deadline = attempt.created_at + timedelta(seconds=float(task_deadline_sec))
            if now > deadline:
                # Просрочка: завершаем попытку (finished_at + time_expired), не только флаг
                attempt = await attempts_service.finish_attempt(db, attempt.id, time_expired=True) or attempt
                attempt.time_expired = True
                check_result = CheckResult(score=0, max_score=check_result.max_score, is_correct=False)
                logger.warning(
                    "POST /attempts/%s/answers: просрочка по задаче task_id=%s, попытка завершена",
                    attempt_id, task.id,
                )

        # 2.3c Y-6 optimistic-PASSED для TA/SA_COM:
        # на submit student получает PASSED моментально (учебный flow не блокируется),
        # teacher проверит через pending-queue (checked_at IS NULL); negative grade
        # вернёт задачу студенту. Если попытка истекла по времени — не подменяем
        # (overdue → честный FAILED, как для остальных типов).
        if (
            task_content.type in ("SA_COM", "TA")
            and not attempt.time_expired
        ):
            check_result = CheckResult(
                score=check_result.max_score,
                max_score=check_result.max_score,
                is_correct=True,
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
    "/attempts/{attempt_id}/attachments",
    response_model=AttemptAttachmentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Загрузить файл к ответу в рамках попытки",
)
async def upload_attempt_attachment(
    attempt_id: int,
    file: UploadFile = File(..., description="Файл для прикрепления к ответу"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> AttemptAttachmentRead:
    """
    Загружает файл в контексте попытки.

    Клиент сохраняет возвращённые метаданные в `StudentAnswer.response.meta.attachments`.
    Это не меняет scoring: вложение только хранится рядом с `answer_json`.
    В рамках одной попытки хранится одно актуальное вложение; повторная успешная загрузка
    заменяет предыдущий файл. Загрузка разрешена только для активной попытки.
    """
    attempt = await attempts_service.get_by_id(db, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Попытка не найдена")
    if not current_user.is_service and current_user.id != attempt.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    if attempt.finished_at is not None or attempt.cancelled_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Загружать вложения можно только для активной попытки.",
        )

    settings.attempt_attachments_upload_dir.mkdir(parents=True, exist_ok=True)

    existing_files = _attempt_attachment_files(attempt_id)
    original_name = _safe_upload_filename(file.filename)
    attachment_id = f"{attempt_id}_{uuid4().hex}_{original_name}"
    file_path = settings.attempt_attachments_upload_dir / attachment_id

    total = 0
    try:
        with open(file_path, "wb") as f:
            while True:
                chunk = await file.read(settings.attachment_chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > settings.max_attachment_size_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Attachment too large. Max {settings.max_attachment_size_bytes} bytes",
                    )
                f.write(chunk)
    except HTTPException:
        try:
            if file_path.exists():
                file_path.unlink()
        except Exception:
            pass
        raise

    for old_file in existing_files:
        if old_file != file_path:
            try:
                os.remove(old_file)
            except FileNotFoundError:
                pass
            except Exception:
                logger.warning(
                    "Не удалось удалить старое вложение attempt_id=%s path=%s",
                    attempt_id,
                    old_file,
                    exc_info=True,
                )

    attachment_url = f"/api/v1/attempts/{attempt_id}/attachments/{attachment_id}"
    logger.info(
        "POST /attempts/%s/attachments: файл загружен filename=%s size=%s",
        attempt_id,
        original_name,
        total,
    )
    return AttemptAttachmentRead(
        attachment_id=attachment_id,
        attachment_url=attachment_url,
        filename=original_name,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=total,
    )


@router.get(
    "/attempts/{attempt_id}/attachments/{attachment_id}",
    summary="Скачать вложение ответа в рамках попытки",
)
async def download_attempt_attachment(
    attempt_id: int,
    attachment_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
):
    attempt = await attempts_service.get_by_id(db, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Попытка не найдена")
    if not current_user.is_service and current_user.id != attempt.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")

    safe_attachment_id = _validate_attempt_attachment_id(attempt_id, attachment_id)
    file_path = settings.attempt_attachments_upload_dir / safe_attachment_id
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment file missing on server")

    media_type = mimetypes.guess_type(safe_attachment_id)[0] or "application/octet-stream"
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=os.path.basename(safe_attachment_id),
    )


@router.post(
    "/attempts/{attempt_id}/cancel",
    response_model=AttemptCancelResponse,
    status_code=status.HTTP_200_OK,
    summary="Аннулировать активную попытку (Learning Engine V1, этап 3.5)",
    responses={
        200: {"description": "Попытка отменена или уже была отменена (идемпотентно)"},
        404: {"description": "Попытка не найдена"},
        409: {"description": "Попытка уже завершена (finished_at задан), отменять нельзя"},
    },
)
async def cancel_attempt(
    attempt_id: int,
    payload: Optional[AttemptCancelRequest] = Body(None, description="Опционально: причина отмены"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> AttemptCancelResponse:
    """
    Аннулировать активную попытку. Идемпотентно: повторный вызов возвращает 200 и already_cancelled=true.
    Завершённые попытки (finished_at задан) отменять нельзя — 409.
    """
    attempt, error, already_cancelled = await attempts_service.cancel_attempt(
        db, attempt_id, reason=payload.reason if payload else None
    )
    if error == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Попытка не найдена")
    if error == "already_finished":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Попытка уже завершена. Аннулировать можно только активную попытку.",
        )
    assert attempt is not None
    return AttemptCancelResponse(
        attempt_id=attempt.id,
        status="cancelled",
        cancelled_at=attempt.cancelled_at,
        already_cancelled=already_cancelled,
    )


@router.post(
    "/attempts/{attempt_id}/finish",
    response_model=AttemptFinishResponse,
    summary="Завершить попытку и вернуть агрегированные результаты",
)
async def finish_attempt(
    attempt_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> AttemptFinishResponse:
    """
    Завершить попытку:

    1. При просрочке по tasks.time_limit_sec помечаем time_expired и завершаем.
    2. Проставить finished_at через AttemptsService.finish_attempt.
    3. Собрать AttemptWithResults (все task_results, суммы баллов, LE V1 поля).
    """
    attempt = await attempts_service.get_by_id(db, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Попытка не найдена")
    if not current_user.is_service and current_user.id != attempt.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    if attempt.cancelled_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Попытка отменена. Завершать можно только активную попытку.",
        )

    time_expired = bool(attempt.time_expired)
    if attempt.finished_at is None:
        time_expired = time_expired or await attempts_service.check_attempt_deadline_expired(db, attempt)
        attempt = await attempts_service.finish_attempt(db, attempt_id, time_expired=time_expired)
        if attempt is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Попытка не найдена")

    attempt_with_results = await _build_attempt_with_results(db, attempt)
    # Learning Engine V1: attempts_used, attempts_limit_effective, last_based_status
    await _enrich_attempt_with_learning_fields(db, attempt_with_results, attempt)
    return AttemptFinishResponse.model_validate(attempt_with_results.model_dump())


@router.get(
    "/attempts/{attempt_id}",
    response_model=AttemptWithResults,
    summary="Получить попытку с результатами по задачам",
)
async def get_attempt(
    attempt_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> AttemptWithResults:
    """
    Вернуть попытку и все результаты по задачам:

    - метаданные попытки (включая time_expired),
    - список task_results в свернутом виде,
    - total_score, total_max_score,
    - опционально attempts_used, attempts_limit_effective, last_based_status (LE V1).
    """
    attempt = await attempts_service.get_by_id(db, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Попытка не найдена")
    if not current_user.is_service and current_user.id != attempt.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")

    attempt_with_results = await _build_attempt_with_results(db, attempt)
    await _enrich_attempt_with_learning_fields(db, attempt_with_results, attempt)
    return attempt_with_results


@router.get(
    "/attempts/by-user/{user_id}",
    response_model=List[AttemptRead],
    summary="Получить попытки пользователя",
)
async def get_attempts_by_user(
    user_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
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
    if not current_user.is_service and current_user.id != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    attempts, total = await attempts_service.get_by_user(
        db,
        user_id=user_id,
        course_id=course_id,
        limit=limit,
        offset=offset,
    )
    return [AttemptRead.model_validate(attempt) for attempt in attempts]
