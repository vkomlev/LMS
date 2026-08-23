from __future__ import annotations

import asyncio
from typing import List, Optional
from datetime import datetime, timedelta, timezone
import os

from fastapi import APIRouter, Depends, Body, File, Form, HTTPException, status, Query, UploadFile
from starlette.responses import StreamingResponse
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
from app.schemas.solution_rules import SolutionRules
from app.schemas.task_content import TaskContent, QUIZ_TASK_TYPES, COMMENT_TASK_TYPES

from app.services.attempts_service import AttemptsService
from app.services.task_results_service import TaskResultsService
from app.services.tasks_service import TasksService
from app.services.checking_service import CheckingService
# tsk-302 этап 3: сам анализ переехал в фоновый тик
# (`code_review_cron_service`), здесь работа только помечается к оценке.
from app.services.code_review_service import pick_code_attachment, pick_code_for_review
# tsk-646: та же очередь, но для развёрнутых текстовых работ — там разбирается
# не чистота кода, а признак ИИ-авторства прозы.
from app.services.text_authorship_service import pick_text_for_review
# tsk-301: единственная дверь прав подписки. Своей проверки здесь быть не должно —
# правило живёт в одном месте на все точки принуждения (пробел П13).
from app.services import entitlements_service
# tsk-575: раскладка файлов-вложений и разбор их имён — в одном модуле,
# потому что читают её ещё и teacher/history-пути (пометка «файл утрачен»).
# tsk-593: сами файлы лежат в объектном хранилище, а не на диске приложения.
from app.services import attachment_storage
from app.services.attempt_attachments import (
    attempt_attachment_names,
    build_attachment_id,
    is_valid_attachment_id,
    names_replaced_by_upload,
    parse_attachment_id,
    safe_upload_filename,
)
from app.services.learning_engine_service import LearningEngineService
from app.services.tasks_acl_service import assert_task_access
from app.services import (
    assignment_rules_service,
    help_requests_service,
    lesson_attendance_service,
    teacher_queue_service,
)
from app.core.config import Settings

from app.utils.exceptions import DomainError
from app.api.error_handlers import retry_after_seconds

import logging

router = APIRouter(tags=["attempts"])
logger = logging.getLogger("api.attempts")
settings = Settings()

attempts_service = AttemptsService()
task_results_service = TaskResultsService()
tasks_service = TasksService()
checking_service = CheckingService()
learning_engine_service = LearningEngineService()


# tsk-302: отбор работ на машинную оценку раньше шёл от ПОМЕТКИ у задания
# (`turtle_sim` / `code_ast`) — функция `_needs_code_review`. На прод-данных это
# оказалось неверным решением: у заданий реального курса пометки нет, а код
# ученик сдаёт вложением (101 работа) или комментарием (370 работ) — оценку из
# них получили 5. Признак теперь берётся из САМОЙ РАБОТЫ
# (`code_review_service.pick_code_for_review`), а пометка задания больше ни на
# что не влияет.


async def _task_attachment_files(attempt_id: int, task_id: int) -> list[str]:
    """
    Файлы-вложения, которые считаются приложенными К ЭТОМУ заданию попытки (tsk-575).

    Гейты ниже (tsk-227 «требуется вложение», tsk-419 «комментарий или файл»)
    раньше смотрели ЛЮБОЙ файл попытки. Попытка охватывает много заданий,
    поэтому один приложенный к заданию 1 скриншот открывал зачёт заданиям
    2..N без единого файла — форс держался только на том, что ученик не
    догадается. Теперь ищем файл этого задания; файлы без метки задания
    (старый клиент, ещё не присылающий `task_id`) засчитываются по-прежнему —
    иначе обновление сервера в одиночку сломало бы приём ответов из бота.

    tsk-593: список приходит из объектного хранилища, поэтому вызов сетевой —
    результат берётся ОДИН раз на задание и переиспользуется обоими гейтами.

    tsk-644: и ограничен по времени. Замер стенда 2026-08-22: при молчащем
    хранилище перечисление держало приём ответа 60 c, после чего ученик всё
    равно получал отказ — то есть минуту он ждал ОТКАЗА. Ответ по сути тот же
    (сдать сейчас нельзя), но приходит он за секунды и говорит, когда вернуться.

    Отказ, а не «примем без проверки»: гейт `requires_attachment` — защита
    (tsk-227/tsk-575), без него зачёт получает работа без файла. Отключать
    защиту на время аварии хранилища значит раздать незаслуженные зачёты именно
    тем, кто сдавал в эти минуты. Выбор подтверждён оператором (tsk-644):
    честный отказ за секунды, а не тихий пропуск проверки и не ожидание.

    :raises HTTPException: 503 с `Retry-After`, если хранилище не ответило.
    """
    try:
        return await asyncio.wait_for(
            attempt_attachment_names(attempt_id, task_id, include_untagged=True),
            timeout=settings.attachment_gate_timeout_sec,
        )
    except (asyncio.TimeoutError, DomainError) as exc:
        # Пауза считается тем же способом, что и при исчерпании пула (tsk-624):
        # с разбросом, чтобы получившие отказ клиенты не вернулись все разом.
        retry_after = retry_after_seconds(settings.attachment_gate_timeout_sec * 2)
        logger.error(
            "POST /attempts/%s/answers: хранилище не ответило за %.0f c "
            "(task_id=%s, %s) → 503, повтор через %d c (tsk-644)",
            attempt_id, settings.attachment_gate_timeout_sec, task_id,
            type(exc).__name__, retry_after,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Файловое хранилище сейчас не отвечает, поэтому ответ с "
                f"вложением принять не можем. Повторите через {retry_after} с — "
                "работа не потеряна."
            ),
            headers={"Retry-After": str(retry_after)},
        ) from exc


def _validate_attempt_attachment_id(attempt_id: int, attachment_id: str) -> str:
    safe_id = safe_upload_filename(attachment_id)
    parsed = parse_attachment_id(attachment_id) if is_valid_attachment_id(attachment_id) else None
    if safe_id != attachment_id or parsed is None or parsed[0] != attempt_id:
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

    # tsk-010: просроченная оплата закрывает решение заданий. Этот адрес — путь
    # в обход learning/start-or-get-attempt, поэтому гейт нужен и здесь.
    # tsk-617: по ученику из тела, а не по вызывающему — сервисный ключ бота
    # иначе оставлял бы должнику решение заданий в Telegram.
    from app.services import payment_access_service

    await payment_access_service.assert_content_allowed(db, payload.user_id)
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
        # tsk-325 (F5): solution_rules может быть JSON null / пусто (1116 импортированных
        # заданий ЕГЭ/Python, аудит tsk-299 — правило автопроверки не заведено). Прежний
        # `SolutionRules.model_validate(task.solution_rules or {})` бросал ошибку на
        # обязательном max_score → приём ответа падал 500. Строим правило через
        # build_solution_rules: непустое валидируем как раньше, пустое деградирует в
        # минимальный валидный объект (max_score из задачи) — SA_COM без правил уйдёт в
        # ручную проверку (is_correct=None) существующим 2.3d, ответ не теряется.
        solution_rules = checking_service.build_solution_rules(
            task.solution_rules, task.max_score
        )

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

        # asyncio.to_thread: tsk-412 добавил turtle_sim — блокирующий вызов
        # песочницы (subprocess.run с таймаутом до неск. секунд). Для остальных
        # типов задач это по-прежнему быстрый sync-вызов, накладные расходы
        # thread-пула пренебрежимо малы.
        check_result: CheckResult = await asyncio.to_thread(
            checking_service.check_task,
            task_content=task_content,
            solution_rules=solution_rules,
            answer=answer,
        )

        # 2.3b.1 tsk-302 (направление 1): статический анализ стиля кода (pylint/
        # radon) для заданий turtle_sim — код ученика уже есть (answer.response.value),
        # анализ независим от результата сверки трассы (даже неверный рисунок может
        # быть написан аккуратно или неряшливо). НАМЕРЕННО не кладётся в check_result:
        # тот эхо-возвращается ученику в AttemptAnswerResult (см. 2.5 ниже) — решение
        # оператора "видимость только teacher/methodist" требует, чтобы отчёт вообще
        # не проходил через объект, отдаваемый в ответе на сдачу. Пишется напрямую в
        # task_results.code_review (см. вызовы create_from_check_result ниже).
        #
        # ПОЧЕМУ code_review, А НЕ metrics (этап 0 tsk-302, 2026-08-06): `metrics`
        # несёт ручную проверку преподавателя, и `manual-check` его ПЕРЕЗАПИСЫВАЕТ
        # целиком — отчёт обнулялся при первой же ручной оценке. Плюс на проде в
        # `metrics` уже 13.8K записей чужой семантики (comment/manual_grant/
        # escalated_at). Отдельная колонка снимает спор за одно поле.
        #
        # ЭТАП 3 (2026-08-07): здесь больше НЕ считаем, а только СТАВИМ В ОЧЕРЕДЬ.
        # Раньше на этом месте синхронно работал pylint (+3-5 с к ответу), теперь
        # оценку делает модель — это внешний сетевой вызов, и держать на нём приём
        # ответа нельзя. Ученик оценку всё равно не видит, а преподаватель открывает
        # работу позже, поэтому фон ничего не теряет (решение оператора 2026-08-06).
        # Разбирает очередь `app/services/code_review_cron_service.py`.
        #
        # Просроченную попытку не оцениваем: балл уже обнулён гейтом 2.3c ниже,
        # тратить на неё вызов модели незачем.
        # Ставим в очередь, только если есть ЧТО оценивать и есть КОМУ: пометка
        # без работающего обработчика оставила бы преподавателю вечное «оценка
        # готовится» (находка ревью Н1). Порог `pick_code_for_review` отсекает
        # ответы-однострочники вида «допиши строку» — оценивать чистоту кода
        # одного слова бессмысленно (находка ревью Б2).
        code_review_report: Optional[dict] = None
        # Признак кодовой работы — САМА РАБОТА, а не пометка у задания. Первая
        # редакция шла от пометки (`code_ast`/`turtle_sim`), и на проде это
        # отсекло почти всё: у заданий реального курса пометки нет, а код лежит
        # либо во вложении (101 работа, формат «приложи файл, впиши вывод»),
        # либо в комментарии (370 работ). Оценку из них получили 5.
        # Поэтому условие одно: удалось ли достать из работы программу —
        # `pick_code_for_review` сам решает, код это или проза.
        #
        # Вместе с пометкой кладём КОПИЮ кода. Причина — файл вложения
        # ИЗМЕНЯЕМ: повторная загрузка по той же паре (попытка, задание)
        # вытесняет предыдущий (`names_replaced_by_upload`, tsk-575). Прочитай
        # фоновый тик файл позже — он взял бы уже другую редакцию решения и
        # приписал её этой сдаче. Снимок прибивает ровно то, что сдали сейчас.
        #
        # (До tsk-575 загрузка стирала файлы ВСЕЙ попытки, и снимок спасал ещё
        # и от этого; тот дефект починен, но изменяемость файла осталась.
        # Историю он не воскрешает: из 101 работы со ссылкой на `.py` файлы
        # уцелели у 8 — они потеряны до починки.)
        #
        # Копия временная: тик перезапишет `code_review` отчётом.
        if not attempt.time_expired and settings.code_review_cron_enabled:
            # tsk-301: гейт подписки стоит ЗДЕСЬ, ДО постановки в очередь и до
            # чтения файла вложения. Позже нельзя: работа уже помечена, и фоновый
            # тик её заберёт — обещание Demo «токены не расходуем» нарушится
            # молча, без единой ошибки в логах (пробел П2 контракта).
            #
            # Ученику отказ НЕ виден: приём ответа проходит как обычно, просто
            # работа не попадает в очередь оценки. Оценку он и так не видит —
            # её читает преподаватель, — поэтому 403 здесь был бы сломанной
            # сдачей вместо отсутствующей услуги.
            gate = await entitlements_service.check(
                db, student_id=attempt.user_id, capability="code_review"
            )
            skip_code_review = entitlements_service.should_block(
                gate, capability="code_review", student_id=attempt.user_id
            )
        else:
            # Просрочка или выключенный тик — не отказ подписки, но очередь
            # всё равно пропускаем.
            skip_code_review = True

        if not skip_code_review:
            # Чтение файла — синхронный ввод-вывод, и теперь оно случается на
            # КАЖДОЙ сдаче с вложением, а не изредка. Уносим с петли событий.
            #
            # tsk-644: и ограничиваем по времени. Замер стенда 2026-08-22: при
            # молчащем объектном хранилище это чтение держало приём ответа 211 c
            # — ученик две с половиной минуты смотрел в экран после «Ответить»,
            # ожидая снимок кода, который заводится РАДИ ПРЕПОДАВАТЕЛЯ и самому
            # ученику не показывается никогда. Такой размен неприемлем: сдача
            # важнее снимка.
            #
            # Не успели — ставим в очередь без снимка. Фоновый тик прочитает
            # файл сам (ветка `code_snapshot or ...` в тике ровно про это).
            # Риск, ради которого снимок заводился, остаётся ровно один: ученик
            # перезальёт файл раньше, чем тик до него дойдёт. Он редкий и стоит
            # дешевле, чем ожидание в две минуты на каждой сдаче.
            #
            # `wait_for` отменяет ОЖИДАНИЕ, но не сам поток: чтение продолжит
            # висеть в фоне до своего таймаута. Поэтому короткий срок здесь —
            # половина решения, вторая половина — явные таймауты клиента
            # хранилища (`S3_READ_TIMEOUT_SEC`), иначе поток общего пула
            # оставался бы занят минутами.
            pick_timed_out = False
            try:
                picked_code = await asyncio.wait_for(
                    asyncio.to_thread(
                        pick_code_for_review,
                        answer.response.value,
                        answer.response.comment,
                        (answer.response.meta or {}).get("attachments"),
                        attempt_id=attempt_id,
                        task_id=task.id,
                    ),
                    timeout=settings.code_pick_timeout_sec,
                )
            except asyncio.TimeoutError:
                picked_code = None
                pick_timed_out = True
                logger.warning(
                    "POST /attempts/%s/answers: снимок кода не снят за %.0f c "
                    "(task_id=%s) — работа в очередь без снимка (tsk-644)",
                    attempt_id, settings.code_pick_timeout_sec, task.id,
                )
            if picked_code:
                code_review_report = {"status": "pending", "kind": "code", "code": picked_code}
            elif task_content.type == "TA":
                # tsk-646: развёрнутый письменный ответ. Разбирается тем же
                # механизмом, но по другому предмету — прозу оценивают не за
                # чистоту, а на признак ИИ-авторства.
                #
                # Только `TA`, и это решение по замеру, а не по осторожности.
                # На прод-корпусе прозы из `SA_COM` детектор дал 5 сработок,
                # и ВСЕ пять — у одного ученика курса про ИИ, где задание
                # прямо просит вставить ответ агента. Там признак означал бы
                # «ученик выполнил задание», то есть ровно ложное обвинение.
                # Замер: docs/qa/2026-08-23-tsk646-text-authorship-calibration.md
                #
                # Текст лежит в `response.text` — у `TA` поля `value` и
                # `comment` пустые всегда (проверено на всех 64 сдачах прода),
                # поэтому кодовая ветка выше по ним ничего и не находит.
                picked_text = pick_text_for_review(answer.response.text)
                if picked_text:
                    code_review_report = {
                        "status": "pending", "kind": "text", "code": picked_text,
                    }
            elif pick_timed_out and pick_code_attachment(
                (answer.response.meta or {}).get("attachments")
            ):
                # tsk-644: не успели прочитать ВЛОЖЕНИЕ — ставим в очередь без
                # снимка: иначе таймаут хранилища молча отменял бы оценку.
                # Условие про вложение обязательно: без него сюда попадали бы и
                # работы, где программы просто нет, а это ровно тот «вечный
                # признак оценка готовится» у преподавателя, ради которого порог
                # и вводился (находки Н1/Б2 ревью этапа 3).
                code_review_report = {"status": "pending", "kind": "code"}

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
        #   - TBL_COM (tsk-366) — тот же тип «с комментарием», ведёт себя как SA_COM.
        #
        # tsk-396 (корень ложного зачёта): признаком «сверять нечем» служит
        # ОТСУТСТВИЕ ЭТАЛОНА, а не `is_correct is None`. У SA_COM/TBL_COM
        # `is_correct=None` возникает по ДВУМ разным причинам: эталона нет
        # (замысел выше) и эталон есть, но включён ручной гейт (tsk-230 —
        # `_check_short_answer` короткозамыкает ДО чтения short_answer-правил).
        # Вторая причина попадала в условие не по замыслу, и заведомо неверный
        # ответ получал score=max_score/is_correct=True: проба на dev показала
        # `state=PASSED` на ответ «999 999» при эталоне «12 516,30» (45 активных
        # заданий прода — 25 ОГЭ-14 курса 1179 и 20 «напиши программу целиком»).
        # `has_reference_answer()` — тот же единый предикат, что у UX-сигнала
        # клиенту (tsk-547), поэтому «сверять нечем» здесь и в форме не разъедутся.
        #
        # `not partial_auto_check` — защита от острого края: валидатор схемы не
        # даёт завести гибридное задание без эталона, но правка `solution_rules`
        # прямо в БД мимо API валидатор обходит, и такое задание получило бы
        # оптимистичный зачёт — ровно тот обход гейта, который эта задача чинит.
        optimistic_manual = task_content.type == "TA" or (
            task_content.type in COMMENT_TASK_TYPES
            and check_result.is_correct is None
            and not solution_rules.has_reference_answer()
            and not solution_rules.partial_auto_check
        )
        if optimistic_manual and not attempt.time_expired:
            # tsk-605: оптимистичный зачёт держится на том, что позже придёт
            # человек. Там, где человека в тарифе нет («ученик работает без
            # преподавателя»), он превращается в полный автозачёт задания, по
            # которому сверять нечем, — а калибровка tsk-590 показала, что без
            # эталона модель не пересчитывает, а подтверждает предъявленное
            # (7.6–19.0 % собственных ошибок). Решение принимает единая дверь
            # прав, здесь оно только применяется.
            #
            # Сегодня отказ НЕ применяется: прод стоит в режиме `guests`, где
            # действуют только отказы `denied_no_plan`, — предохранитель
            # включится вместе с гейтом подписки (tsk-301), которому и
            # принадлежит решение, куда девать такую работу.
            verdict_gate = await entitlements_service.check_machine_verdict(
                db,
                student_id=attempt.user_id,
                task_type=task_content.type,
                solution_rules=solution_rules,
            )
            hold_for_human = entitlements_service.should_block(
                verdict_gate,
                capability="machine_verdict",
                student_id=attempt.user_id,
            )
        else:
            hold_for_human = False

        if optimistic_manual and not attempt.time_expired and not hold_for_human:
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
        # (_task_attachment_files: файлы этого задания в хранилище, кладётся
        # эндпоинтом POST /attempts/{id}/attachments). answer.response.meta.attachments
        # НЕ используется — это клиентские данные из тела запроса, их можно подделать
        # (`meta:{attachments:[{}]}`) и обойти форс без единого файла. Оба клиента
        # (SPW, TG_LMS) грузят реальный файл до сдачи, поэтому доверие только
        # хранилищу честные пути не ломает. При истёкшем времени попытка уже завершена и
        # провалена (score=0 выше) — гейт не трогаем, вложить файл уже нельзя.
        #
        # tsk-575: файл ищем У ЭТОГО ЗАДАНИЯ, а не по всей попытке. Раньше хватало
        # любого файла попытки — приложил скриншот к заданию 1, и задания 2..N
        # проходили форс без вложения.
        # tsk-593: список файлов задания — сетевой запрос в хранилище, поэтому
        # снимаем его ОДИН раз на задание и переиспользуем в обоих гейтах ниже.
        # Раньше здесь стояло три обращения к диску подряд, и с хранилищем это
        # были бы три запроса на каждый принятый ответ.
        task_attachment_files: Optional[list[str]] = None
        if not attempt.time_expired and (
            solution_rules.requires_attachment or task_content.type in COMMENT_TASK_TYPES
        ):
            task_attachment_files = await _task_attachment_files(attempt.id, task.id)

        if solution_rules.requires_attachment and not attempt.time_expired:
            has_attachment = bool(task_attachment_files)
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

        # 2.3f tsk-419: для SA_COM/TBL_COM (задачи "с комментарием") обязателен
        # комментарий ИЛИ файл — иначе ответ можно подобрать устно/угадыванием без
        # доказательства решения (QA tsk-414, пример id-149 «курсор→танцор»).
        # В отличие от 2.3e (requires_attachment — per-task opt-in флаг из
        # solution_rules), это универсальное правило по ТИПУ задания, решение
        # оператора. Гейт после форса вложения намеренно — если вложение уже
        # обязательно и его нет, сообщение 2.3e важнее (файл конкретно требуется),
        # а не общее "комментарий или файл".
        if (
            task_content.type in COMMENT_TASK_TYPES
            and not attempt.time_expired
            and not (
                solution_rules.requires_attachment
                and not bool(task_attachment_files)
            )
        ):
            has_comment = bool((answer.response.comment or "").strip())
            has_attachment = bool(task_attachment_files)
            if not has_comment and not has_attachment:
                logger.info(
                    "POST /attempts/%s/answers: task_id=%s (%s) без комментария и вложения → не зачёт (tsk-419)",
                    attempt_id, task.id, task_content.type,
                )
                check_result = CheckResult(
                    score=0,
                    max_score=check_result.max_score,
                    is_correct=False,
                    feedback=CheckFeedback(
                        general=(
                            "Добавьте комментарий (например, ход решения) или приложите файл — "
                            "без этого ответ не засчитывается."
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
                code_review=code_review_report,
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
                code_review=code_review_report,
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

        # 2.4c tsk-339: если ответ решил задание (PASSED) — закрыть открытую
        # заявку blocked_limit по нему. Блокировка снялась не через выдачу
        # лимита учителем (tsk-335, там закрытие явное), а тем, что ученик
        # справился сам — без этого шага заявка висела бы в очереди навсегда
        # (найдено живым прогоном tsk-335/336, 9 стухших заявок на проде).
        # Soft-fail по тому же паттерну, что 2.4b: не ломает учебный поток.
        try:
            state_after = await learning_engine_service.compute_task_state(
                db, student_id=attempt.user_id, task_id=task.id,
            )
            if state_after.state == "PASSED":
                closed = await help_requests_service.close_blocked_limit_if_resolved(
                    db,
                    student_id=attempt.user_id,
                    task_id=task.id,
                    resolution_comment="Задание решено учеником самостоятельно",
                )
                if closed is not None:
                    await db.commit()
        except Exception:
            logger.warning(
                "tsk-339: auto-close blocked_limit failed: attempt=%s task=%s",
                attempt.id, task.id, exc_info=True,
            )
            try:
                await db.rollback()
            except Exception:
                pass

        # 2.4d tsk-439: реальное учебное действие (сдача ответа) во время
        # окна занятия автоматически подтверждает явку — не дожидаясь клика
        # "Я на занятии". Soft-fail по тому же паттерну, что 2.4b/2.4c: явка
        # никогда не должна ломать основной поток сдачи задания.
        try:
            await lesson_attendance_service.auto_confirm_if_in_progress(
                db, student_id=attempt.user_id,
            )
        except Exception:
            logger.warning(
                "tsk-439: auto-confirm attendance failed: attempt=%s user=%s",
                attempt.id, attempt.user_id, exc_info=True,
            )
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
    task_id: Optional[int] = Form(
        None,
        description=(
            "Задание, к ответу на которое прикладывается файл. Определяет, что именно "
            "заменит повторная загрузка (tsk-575). Не передан — файл считается "
            "вложением попытки целиком, как до tsk-575."
        ),
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> AttemptAttachmentRead:
    """
    Загружает файл в контексте попытки.

    Клиент сохраняет возвращённые метаданные в `StudentAnswer.response.meta.attachments`.
    Это не меняет scoring: вложение только хранится рядом с `answer_json`.

    Актуальное вложение — ОДНО НА ПАРУ «попытка + задание» (`task_id`), а не одно на
    попытку: попытка охватывает много заданий, и до tsk-575 загрузка файла к заданию 2
    стирала файл задания 1 вместе со ссылкой в уже сданной работе. Повторная загрузка
    по тому же заданию заменяет прежний файл, по другому — не трогает его.

    `task_id` необязателен ради клиентов, которые его ещё не шлют: такая загрузка
    заменяет только прежние файлы попытки БЕЗ метки задания. Загрузка разрешена
    только для активной попытки.
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

    if task_id is not None and task_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="task_id должен быть положительным",
        )

    # Список того, что вытесняет эта загрузка, снимаем ДО записи нового файла:
    # так в него заведомо не попадёт он сам.
    existing_files = await names_replaced_by_upload(attempt_id, task_id)
    original_name = safe_upload_filename(file.filename)
    attachment_id = build_attachment_id(attempt_id, task_id, original_name)

    # tsk-593: файл уходит в объектное хранилище. Отказ хранилища — это 503 и
    # НЕ записанное вложение: молчаливый успех дал бы ученику ощущение сданной
    # работы и ссылку в `answer_json`, за которой нет файла.
    try:
        total, content_type = await attachment_storage.store_upload(
            attachment_storage.ATTEMPTS, attachment_id, file
        )
    except DomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    for old_name in existing_files:
        if old_name == attachment_id:
            continue
        try:
            await attachment_storage.delete(attachment_storage.ATTEMPTS, old_name)
        except DomainError:
            # Вытеснить прежний файл не вышло — новый уже записан, приём ответа
            # ломать из-за этого нельзя. Останется лишний файл, а не потерянный.
            logger.warning(
                "tsk-593: не удалось удалить старое вложение attempt_id=%s task_id=%s имя=%s",
                attempt_id, task_id, old_name, exc_info=True,
            )

    attachment_url = f"/api/v1/attempts/{attempt_id}/attachments/{attachment_id}"
    logger.info(
        "POST /attempts/%s/attachments: файл загружен task_id=%s filename=%s size=%s заменено=%s хранилище=%s",
        attempt_id,
        task_id,
        original_name,
        total,
        len(existing_files),
        "s3" if attachment_storage.s3_enabled() else "диск",
    )
    return AttemptAttachmentRead(
        attachment_id=attachment_id,
        attachment_url=attachment_url,
        filename=original_name,
        content_type=content_type,
        size_bytes=total,
    )


@router.get(
    "/attempts/{attempt_id}/attachments/{attachment_id}",
    summary="Скачать вложение ответа в рамках попытки",
    responses={
        200: {"description": "Файл вложения"},
        403: {"description": "Не владелец попытки и не преподаватель-ревьюер"},
        404: {"description": "Попытка не найдена либо имя вложения некорректно/чужое"},
        410: {
            "description": (
                "Имя вложения разобрано, но файла на сервере нет: утрачен (tsk-575) "
                "или вытеснен перезаливкой того же задания. Восстановлению не подлежит."
            )
        },
    },
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

    # tsk-593: содержимое приходит из объектного хранилища и отдаётся потоком
    # через приложение. Прямая ссылка на бакет наружу не выдаётся намеренно:
    # переадресация обошла бы проверку прав выше, а в бакете объект читает
    # любой, кто знает ключ.
    try:
        opened = await attachment_storage.open_stream(
            attachment_storage.ATTEMPTS, safe_attachment_id
        )
    except DomainError as exc:
        # Хранилище не ответило — это НЕ «файла нет»: 410 здесь означал бы
        # «файл утрачен навсегда», и преподаватель зря попросил бы перезалить.
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    if opened is None:
        # 410, а не 404: имя разобрано, попытка та самая — файл БЫЛ и его больше
        # нет. Различать «утрачен дефектом хранения (tsk-575)» и «вытеснен
        # перезаливкой того же задания» по хранилищу нельзя, поэтому текст
        # говорит о факте, а не о причине. 404 здесь врал: он читается как
        # «такого файла и не было», и преподаватель шёл искать ошибку у себя.
        logger.warning(
            "GET /attempts/%s/attachments/%s: файла нет в хранилище (tsk-575)",
            attempt_id, safe_attachment_id,
        )
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                "Файл вложения утрачен на сервере и восстановлению не подлежит. "
                "Попросите ученика приложить файл заново."
            ),
        )

    stream, media_type = opened
    return StreamingResponse(
        stream,
        media_type=media_type,
        headers={
            "Content-Disposition": attachment_storage.content_disposition(
                os.path.basename(safe_attachment_id)
            )
        },
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
