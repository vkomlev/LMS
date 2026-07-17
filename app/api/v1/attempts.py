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
from sqlalchemy import select, text

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
    CheckFeedback,
)
from app.schemas.task_content import TaskContent, QUIZ_TASK_TYPES
from app.schemas.solution_rules import SolutionRules

from app.services.attempts_service import AttemptsService
from app.services.task_results_service import TaskResultsService
from app.services.tasks_service import TasksService
from app.services.checking_service import CheckingService
from app.services.learning_engine_service import LearningEngineService
from app.services import assignment_rules_service
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
    # tsk-264: лимит — в границах корня, которым открыта попытка.
    state = await learning_engine_service.compute_task_state(
        db, attempt.user_id, first_task_id, root_course_id=attempt.root_course_id
    )
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
    # tsk-264: корень определяем и здесь, а не только в start-or-get-attempt.
    # Попытка с пустым корнем не расходует лимит ни в одном курсе, поэтому без
    # резолва этот эндпоинт стал бы способом выдать себе бесконечные попытки.
    root_course_id: int | None = None
    if payload.course_id is not None:
        try:
            root_course_id = await learning_engine_service.resolve_attempt_root(
                db,
                student_id=payload.user_id,
                course_id=payload.course_id,
                requested_root_course_id=payload.root_course_id,
            )
        except DomainError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
    attempt = await attempts_service.create_attempt(
        db=db,
        user_id=payload.user_id,
        course_id=payload.course_id,
        root_course_id=root_course_id,
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
        409: {
            "description": "Повторный ответ на квиз-вопрос (SC_Qw/MC_Qw): допускается только одна попытка",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Квиз-вопрос допускает только одну попытку. Ответ уже принят."
                    }
                }
            },
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

        # 2.1.1 tsk-264: результат обязан лечь в тот же контекст, в котором потом
        # считается лимит. Лимит считается по корню попытки, поэтому ответ на
        # задание ВНЕ дерева этого корня не считался бы нигде — то есть давал бы
        # неограниченные попытки. Пустой корень (путь неизвестен) не проверяем:
        # там лимит и так не расходуется, и это осознанное поведение (см. tsk-264).
        if attempt.root_course_id is not None and task.course_id is not None:
            if not await learning_engine_service.root_contains_course(
                db, attempt.root_course_id, task.course_id
            ):
                logger.warning(
                    "POST /attempts/%s/answers: задание вне дерева попытки "
                    "(task_id=%s task.course_id=%s attempt.root_course_id=%s)",
                    attempt_id, task.id, task.course_id, attempt.root_course_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Задание {task.id} не входит в курс "
                        f"{attempt.root_course_id}, в рамках которого открыта попытка."
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

        # 2.3a Квиз (SC_Qw/MC_Qw, tsk-124): ровно одна попытка. Если по задаче уже
        # есть ответ в неотменённой попытке — повтор запрещён (иначе задвоится
        # накопление scale_scores и сломается интерпретация шкал). Сервер —
        # источник истины, не полагаемся только на лимит во фронте.
        if task_content.type in QUIZ_TASK_TYPES:
            existing = await db.execute(
                text("""
                    SELECT 1
                    FROM task_results tr
                    INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
                    WHERE tr.user_id = :user_id AND tr.task_id = :task_id
                    LIMIT 1
                """),
                {"user_id": attempt.user_id, "task_id": task.id},
            )
            if existing.first() is not None:
                logger.info(
                    "POST /attempts/%s/answers: повторный ответ на квиз task_id=%s отклонён (одна попытка)",
                    attempt_id, task.id,
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Квиз-вопрос допускает только одну попытку. Ответ уже принят.",
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

        # 2.3c optimistic-PASSED — для TA и БЕЗ-эталонного SA_COM (tsk-210):
        # optimistic-PASSED нужен там, где авто-сверять нечем и вердикт ставит
        # только учитель вручную. На submit ставим score=max_score/is_correct=True,
        # чтобы учебный поток не блокировался; teacher проверит через pending-queue
        # (checked_at IS NULL), negative grade вернёт задачу студенту.
        #   - TA: эталона нет в принципе (checking_service → is_correct=None).
        #   - SA_COM без правил (short_answer не задан) → checking_service тоже
        #     вернул is_correct=None: сверять нечем, ведём себя как TA, иначе
        #     задача «зависнет» (is_correct=None не пройдёт фильтр очереди учителя).
        #
        # SA_COM С эталоном (accepted_answers/regex) НЕ подменяем: первичный
        # вердикт обязан идти от сверки с эталоном (вызов checking_service выше).
        # Учитель делает ВТОРИЧНУЮ проверку (чистота кода, не ИИ ли сгенерирован)
        # только для первично-верных ответов — см. фильтр `is_correct IS TRUE` в
        # teacher_queue/escalation. Прежний blanket-override ставил здесь
        # score=max_score/is_correct=True ДАЖЕ на неверные ответы (ученик видел
        # «Верно» на заведомо неверный ответ) — баг P0 из обратной связи QA
        # (tsk-210, находка A1).
        #
        # Если попытка истекла по времени — не подменяем (overdue → честный
        # FAILED, как для остальных типов).
        optimistic_manual = task_content.type == "TA" or (
            task_content.type == "SA_COM" and check_result.is_correct is None
        )
        if optimistic_manual and not attempt.time_expired:
            check_result = CheckResult(
                score=check_result.max_score,
                max_score=check_result.max_score,
                is_correct=True,
            )

        # 2.3d tsk-227: форс вложения. Если задача требует файл-подтверждение
        # (solution_rules.requires_attachment), а в попытке нет РЕАЛЬНО загруженного
        # файла — ответ НЕ засчитывается, даже если авто-проверка (или оптимистичный
        # пасс SA_COM выше) поставила is_correct=True. Сервер — источник истины;
        # клиент только показывает обязательную загрузку. Гейт стоит ПОСЛЕ
        # оптимистичного пасса, поэтому перекрывает его (см. R4 спека tsk-227).
        #
        # БЕЗОПАСНОСТЬ: детект ТОЛЬКО по реально загруженному файлу
        # (_attempt_attachment_files: {attempt_id}_* в upload-dir, кладётся
        # эндпоинтом POST /attempts/{id}/attachments). answer.response.meta.attachments
        # НЕ используется — это клиентские данные из тела запроса, их можно подделать
        # (`meta:{attachments:[{}]}`) и обойти форс без единого файла. Оба клиента
        # (SPW, TG_LMS) грузят реальный файл до сдачи, поэтому доверие только диску
        # честные пути не ломает. При истёкшем времени попытка уже завершена и
        # провалена (score=0 выше) — гейт не трогаем, вложить файл уже нельзя.
        if solution_rules.requires_attachment and not attempt.time_expired:
            has_attachment = bool(_attempt_attachment_files(attempt.id))
            if not has_attachment:
                logger.info(
                    "POST /attempts/%s/answers: requires_attachment task_id=%s без вложения → не зачёт (tsk-227)",
                    attempt_id, task.id,
                )
                check_result = CheckResult(
                    score=0,
                    max_score=check_result.max_score,
                    is_correct=False,
                    feedback=CheckFeedback(
                        general=(
                            "Прикрепите файл-подтверждение (скриншот/файл) — "
                            "без вложения задание не засчитывается."
                        )
                    ),
                )

        # 2.4 Записываем в task_results
        task_result = await task_results_service.create_from_check_result(
            db=db,
            attempt_id=attempt.id,
            task_id=task.id,
            user_id=attempt.user_id,
            answer=answer,
            check_result=check_result,
            source_system=attempt.source_system,
        )

        # 2.4b tsk-031: оценка правил назначения по ответу (answer_value / task_failed).
        # Soft-fail: движок назначения никогда не ломает учебный поток.
        try:
            await assignment_rules_service.evaluate_rules_for_answer(
                db,
                student_id=attempt.user_id,
                task_id=task.id,
                answer=answer,
                check_result=check_result,
                attempt_id=attempt.id,
                task_result_id=getattr(task_result, "id", None),
            )
        except Exception:
            logger.warning(
                "assignment rules (answer) failed: attempt=%s task=%s",
                attempt.id, task.id, exc_info=True,
            )
            # Восстановить сессию: иначе aborted-транзакция сломает запись
            # следующего task_result в этом же цикле.
            try:
                await db.rollback()
            except Exception:
                pass

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

    # tsk-031: оценка правил назначения по завершённой попытке (course_failed).
    # Soft-fail: движок назначения не ломает завершение попытки.
    try:
        await assignment_rules_service.evaluate_rules_for_attempt(
            db,
            student_id=attempt.user_id,
            attempt_id=attempt.id,
        )
    except Exception:
        logger.warning(
            "assignment rules (attempt finish) failed: attempt=%s",
            attempt.id, exc_info=True,
        )
        # Восстановить сессию перед сборкой ответа по результатам попытки.
        try:
            await db.rollback()
        except Exception:
            pass

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
