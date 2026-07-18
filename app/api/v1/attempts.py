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
from app.services.tasks_acl_service import assert_task_access
from app.services import assignment_rules_service, teacher_queue_service
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
            "description": (
                "Попытка уже завершена, истекло время, задание вне дерева корня "
                "попытки либо путь к заданию неоднозначен при исчерпанном лимите "
                "(нужен root_course_id, tsk-269)"
            ),
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
                        "root_required": {
                            "summary": "Неоднозначный путь при исчерпанном лимите",
                            "value": {
                                "detail": (
                                    "Задание входит в несколько ваших курсов, и в одном "
                                    "из них лимит попыток исчерпан. Укажите root_course_id — "
                                    "курс, в рамках которого отправляется ответ."
                                )
                            }
                        },
                    }
                }
            }
        },
        403: {
            "description": (
                "Доступ запрещён: попытка принадлежит другому пользователю, либо "
                "ученик не зачислён на курс задания (tsk-272). Сервисный ключ "
                "(X-API-Key) и роли teacher/methodist/admin проверку проходят."
            ),
        },
        404: {
            "description": "Попытка не найдена",
        },
        409: {
            "description": (
                "Ответ не принят: повторный ответ на квиз-вопрос (SC_Qw/MC_Qw, "
                "допускается только одна попытка) либо исчерпан лимит попыток по "
                "заданию в рамках курса, которым открыта попытка (tsk-269)"
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "quiz_repeat": {
                            "summary": "Повторный ответ на квиз",
                            "value": {
                                "detail": "Квиз-вопрос допускает только одну попытку. Ответ уже принят."
                            }
                        },
                        "attempts_limit": {
                            "summary": "Лимит попыток исчерпан",
                            "value": {
                                "detail": "Лимит попыток по заданию исчерпан (3 из 3)."
                            }
                        },
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

        # 2.1.0b tsk-272: ACL доступа к заданию. Раньше приём ответа не проверял,
        # записан ли ученик на курс задания: чтение задания защищено assert_task_access
        # (GET /tasks/*), а запись task_results — нет. Ученик без единой активной
        # user_courses открывал попытку на любой курс и отвечал (коды [200,...],
        # task_results рос) — подтверждено на живых данных. Та же проверка, что на
        # чтении, ставит запись и чтение в один контур доступа.
        #
        # Bypass встроен в helper: is_service (X-API-Key — TG_LMS, CB CLI) и роли
        # teacher/methodist/admin проходят. Гости идут отдельным эндпоинтом
        # (/learning/guest/attempts), сюда не попадают. Гейт per-item, до записи —
        # чтобы отказ по одному заданию не оставлял частичный результат.
        await assert_task_access(
            db, current_user=current_user, task_course_id=task.course_id
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

        # 2.3b tsk-269: форс лимита попыток. Раньше лимит жил только в ВЫДАЧЕ
        # (compute_task_state → next-item/state, me_service → syllabus): интерфейс
        # показывал «заблокировано», но приём ответа ничего не проверял, и клиент,
        # зовущий API напрямую, отвечал сколько угодно раз. Сервер — источник истины.
        #
        # Спрашиваем ТОТ ЖЕ compute_task_state, что и выдача, а не считаем лимит
        # заново: вторая копия формулы разошлась бы с первой (override, квиз,
        # PASS_THRESHOLD). BLOCKED_LIMIT возвращается только когда лимит исчерпан
        # И задание не сдано — сдавший ученик не блокируется, как и в выдаче.
        #
        # tsk-264: счёт — в границах корня. Корень берём у попытки, а если его там
        # нет — доопределяем ПО САМОМУ ЗАДАНИЮ. Полагаться только на
        # `attempt.root_course_id` нельзя: `course_id` в теле POST /attempts
        # опционален, и попытка без него создаётся с пустым корнем. Тогда и этот
        # гейт, и проверка дерева 2.1.1 молча выключались бы — клиент убирал одно
        # поле из запроса и отвечал бесконечно на любое задание. Ровно та модель
        # угрозы, которую tsk-269 закрывает (находка независимого ревью).
        #
        # Пустой корень остаётся только там, где путь ОБЪЕКТИВНО неоднозначен (узел
        # под несколькими активными курсами ученика) — там не форсим: пришлось бы
        # считать по всем курсам сразу, а это ровно жалоба tsk-261 A7
        # (переиспользуемый узел мёртв в новом курсе). Это осознанная цена, и она
        # не оправдывает пропуск там, где корень восстанавливается однозначно.
        #
        # Старые попытки с пустым корнем (на проде 7) от этого лимит не начинают
        # расходовать: счёт в compute_task_state идёт по `a.root_course_id = :root`,
        # и их результаты по-прежнему не попадают ни в один корень.
        #
        # Квиз (SC_Qw/MC_Qw) сюда не доходит: его 409 отдан выше (2.3a) с более
        # точной формулировкой — про одну попытку навсегда, а не про исчерпанный лимит.
        effective_root_id = attempt.root_course_id
        if effective_root_id is None and task.course_id is not None:
            try:
                effective_root_id = await learning_engine_service.resolve_attempt_root(
                    db,
                    student_id=attempt.user_id,
                    course_id=task.course_id,
                )
            except DomainError:
                # Корень восстановить нечем — ведём себя как при неоднозначном пути.
                effective_root_id = None

        # Путь так и остался неоднозначным (узел под несколькими активными курсами
        # ученика). Гадать корень нельзя — попытка спишется не в том курсе. Но и
        # молча пропускать нельзя: счёт по корню не растёт, значит попытки тут
        # БЕСКОНЕЧНЫ, а прогресс (PASSED) в compute_task_state корнем не
        # фильтруется — перебором добывается зачёт в том самом корне, где ученик
        # заблокирован (находка Б2 независимого ревью, воспроизведена).
        #
        # Решение оператора: спрашиваем корень (400) ТОЛЬКО когда лимит на кону —
        # ученик уже заблокирован хотя бы в одном из своих корней. Честный ученик
        # с оставшимися попытками 400 никогда не увидит: цена падает только на
        # подозрительный случай. SPW корень знает (useRootCourseId) и сюда не
        # попадёт; TG_LMS на переиспользуемом узле — отдельная задача.
        if effective_root_id is None and task.course_id is not None:
            candidate_roots = await learning_engine_service.list_active_roots_of_node(
                db, student_id=attempt.user_id, course_id=task.course_id
            )
            for candidate_root_id in candidate_roots:
                candidate_state = await learning_engine_service.compute_task_state(
                    db,
                    student_id=attempt.user_id,
                    task_id=task.id,
                    root_course_id=candidate_root_id,
                )
                if candidate_state.state == "BLOCKED_LIMIT":
                    logger.info(
                        "POST /attempts/%s/answers: неоднозначный путь при исчерпанном "
                        "лимите (task_id=%s course_id=%s roots=%s blocked_in=%s) → 400 (tsk-269)",
                        attempt_id, task.id, task.course_id, candidate_roots, candidate_root_id,
                    )
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            "Задание входит в несколько ваших курсов, и в одном из них "
                            "лимит попыток исчерпан. Укажите root_course_id — курс, "
                            "в рамках которого отправляется ответ."
                        ),
                    )

        if effective_root_id is not None:
            task_state = await learning_engine_service.compute_task_state(
                db,
                student_id=attempt.user_id,
                task_id=task.id,
                root_course_id=effective_root_id,
            )
            if task_state.state == "BLOCKED_LIMIT":
                logger.info(
                    "POST /attempts/%s/answers: лимит попыток исчерпан "
                    "(task_id=%s root_course_id=%s used=%s limit=%s) → 409 (tsk-269)",
                    attempt_id, task.id, effective_root_id,
                    task_state.attempts_used, task_state.attempts_limit_effective,
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Лимит попыток по заданию исчерпан "
                        f"({task_state.attempts_used} из {task_state.attempts_limit_effective})."
                    ),
                )

        check_result: CheckResult = checking_service.check_task(
            task_content=task_content,
            solution_rules=solution_rules,
            answer=answer,
        )

        # 2.3c Learning Engine V1: таймлимит из tasks.time_limit_sec; при просрочке score=0
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

        # 2.3d optimistic-PASSED — для TA и БЕЗ-эталонного SA_COM (tsk-210):
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

        # 2.3e tsk-227: форс вложения. Если задача требует файл-подтверждение
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

        # 2.4 Записываем в task_results.
        #
        # tsk-273: запись под точечным advisory-замком против гонки (TOCTOU).
        # Гейт 2.3b читает счёт попыток и решает 409, но между чтением и записью
        # нет сериализации, а repos/base.py коммитит каждую запись отдельно. Два
        # одновременных ответа при «лимит-1» оба прочитали бы одинаковый счёт, оба
        # прошли бы гейт и оба записались бы — task_results > limit (воспроизведено:
        # 6 одновременных ответов при лимите 3 → 6-7 записей). Прецедент
        # pg_advisory_xact_lock (learning.py) в исходном виде не переносится: он
        # отпускается на первом commit, а тут commit на каждую запись; наивный
        # session-lock тоже теряется на commit (соединение уходит в пул) — оба
        # проверены пробником. Решение: атомарная секция {замок → ПЕРЕСЧЁТ лимита →
        # запись → один commit} в одной транзакции. Замок xact-scoped и держится до
        # commit, значит второй запрос пересчитает счёт уже ПОСЛЕ записи первого и
        # получит 409. Ключ — (user, task:root): контендят только ответы на одно
        # задание в одном корне, разные задания друг другу не мешают.
        #
        # effective_root_id is None — путь неизвестен/неоднозначен, лимит тут и так
        # не форсится (2.3b: null-root → 200, ambiguous+исчерпан → 400 выше). Замок
        # там не нужен: сериализовать нечего.
        if effective_root_id is not None:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(:k1, hashtext(:k2))"),
                {"k1": attempt.user_id, "k2": f"{task.id}:{effective_root_id}"},
            )
            locked_state = await learning_engine_service.compute_task_state(
                db,
                student_id=attempt.user_id,
                task_id=task.id,
                root_course_id=effective_root_id,
            )
            if locked_state.state == "BLOCKED_LIMIT":
                # Гонку выиграл конкурент: пока мы шли к записи, лимит добрали.
                # Откат тут НЕ делаем: db.rollback() истёк бы ORM-объекты attempt/task
                # (rollback экспайрит всегда, даже при expire_on_commit=False), и
                # следующее же обращение task.id дёрнуло бы ленивую загрузку вне
                # greenlet-контекста → MissingGreenlet. Замок xact-scoped и так
                # отпустится при закрытии сессии на выходе из запроса — как и
                # существующие 409/400 выше, которые тоже просто raise без rollback.
                logger.info(
                    "POST /attempts/%s/answers: лимит добран конкурентом под замком "
                    "(task_id=%s root_course_id=%s used=%s limit=%s) → 409 (tsk-273)",
                    attempt_id, task.id, effective_root_id,
                    locked_state.attempts_used, locked_state.attempts_limit_effective,
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Лимит попыток по заданию исчерпан "
                        f"({locked_state.attempts_used} из {locked_state.attempts_limit_effective})."
                    ),
                )
            task_result = await task_results_service.create_from_check_result(
                db=db,
                attempt_id=attempt.id,
                task_id=task.id,
                user_id=attempt.user_id,
                answer=answer,
                check_result=check_result,
                source_system=attempt.source_system,
                commit=False,
            )
            await db.commit()  # фиксируем запись и отпускаем замок атомарно
        else:
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
        # tsk-298 Фаза 2: сверх владельца-ученика и service-key — преподаватель,
        # авторизованный на проверку работы этой попытки (REVIEW_ACL:
        # teacher на course-tree ИЛИ methodist), тоже может скачать вложение
        # ответа для оценки в веб-портале.
        if not await teacher_queue_service.teacher_can_review_attempt(
            db, attempt_id, current_user.id
        ):
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
