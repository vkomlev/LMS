"""
Learning API V1 (этап 3): next-item, materials/complete, tasks/start-or-get-attempt, state, request-help.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Path, Body, status
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, get_current_user
from app.api.error_handlers import is_deadlock_error
from app.auth.current_user import CurrentUser
from app.models.attempts import Attempts
from app.models.tasks import Tasks
from app.schemas.solution_rules import SolutionRules
from app.schemas.task_content import SHORT_ANSWER_TASK_TYPES
from app.schemas.learning_api import (
    NextItemResponse,
    MaterialCompleteRequest,
    MaterialCompleteResponse,
    LearningSkipRequest,
    LearningSkipResponse,
    StartOrGetAttemptRequest,
    StartOrGetAttemptResponse,
    TaskStateResponse,
    RequestHelpRequest,
    RequestHelpResponse,
    StudentHelpRequestResponse,
    HelpRequestReopenResponse,
    IndividualReviewResponse,
    RateReviewRequest,
    RateReviewResponse,
    HintEventRequest,
    HintEventResponse,
)
from app.schemas.learning_engine import NextItemResult
from app.services.learning_engine_service import LearningEngineService
from app.services.learning_events_service import (
    record_help_requested,
    record_hint_open,
    record_task_opened,
    set_material_completed,
    set_material_skipped,
    set_task_skipped,
)
from app.services.help_requests_service import (
    get_or_create_help_request,
    get_or_create_blocked_limit_help_request,
    get_student_help_request,
    reopen_help_request,
    request_individual_review,
    rate_individual_review,
)
from app.services import payment_access_service
# tsk-673: тариф «Выпускник» закрывает работу в курсе (сдачу), оставляя чтение.
from app.services import graduation_service
from app.services import lesson_attendance_service
# tsk-301: единственная дверь прав подписки — своей проверки здесь быть не должно.
from app.services import entitlements_service
from app.services.attempts_service import AttemptsService
from app.services.tasks_service import TasksService
from app.services.materials_service import MaterialsService
from app.services.users_service import UsersService
from app.utils.exceptions import DomainError

router = APIRouter(prefix="/learning", tags=["learning"])
logger = logging.getLogger("api.learning")

#: tsk-010/tsk-617: учебные пути ниже закрыты, если оплата просрочена. Ответ
#: описан явно, потому что клиент обязан отличать его от «нет прав»: тело несёт
#: `payload.code = payment_overdue`, сумму, месяцы и ссылку на кабинет оплаты.
#: Гейт проверяет УЧЕНИКА из запроса, а не вызывающего, — сервисный ключ бота от
#: него не освобождает.
_PAYMENT_403_DESCRIPTION = (
    "tsk-010: занятия закрыты за просроченную оплату — "
    "`payload.code = payment_overdue` (сумма, месяцы, `payments_url`); "
    "tsk-673: тариф закрывает работу в курсе — `payload.code = course_work_closed` "
    "(так устроен «Выпускник»: материалы читаются, ответы не принимаются); "
    "либо чужой `student_id`."
)
_PAYMENT_403 = {403: {"description": _PAYMENT_403_DESCRIPTION}}

learning_service = LearningEngineService()
attempts_service = AttemptsService()
tasks_service = TasksService()
materials_service = MaterialsService()
users_service = UsersService()


# ----- GET /learning/next-item -----
# Внимание: GET выполняет запись в БД (upsert student_course_state при проверке зависимостей).
# Это создаёт write-амплификацию при частых вызовах; для read-only сценариев можно вынести
# обновление состояния в отдельный вызов или кэш.


async def _resolve_next_item_with_retry(
    db: AsyncSession,
    student_id: int,
    *,
    root_course_id: int | None,
    after_material_id: int | None,
    after_task_id: int | None,
) -> NextItemResult:
    """Разрешить следующий шаг, пережив одну взаимоблокировку (tsk-626).

    **Почему повтор здесь уместен, хотя обычно повторы — дело клиента.**
    Взаимоблокировку PostgreSQL разрешает сам: одну из транзакций он снимает
    ЦЕЛИКОМ, и снятая сторона не оставляет за собой ничего — ни записей кеша,
    ни писем методисту (`escalate_course_completion` пишет только в базу, в той
    же транзакции). Значит повтор здесь — не «попробовать ещё раз и надеяться»,
    а буквально то же самое с чистого листа, и вторая попытка почти всегда
    проходит: соперник к этому моменту уже завершился.

    **Почему только одна попытка и только здесь.** Основное лечение —
    блокировка ученика перед записью кеша (`learning_engine_service.
    upsert_course_state`), после неё этот класс взаимоблокировок не собирается
    вовсе. Повтор оставлен сеткой на случай ДРУГОГО порядка захвата, который мы
    ещё не знаем; вторая и третья попытки такой случай уже не спасли бы, а
    только удлинили бы ожидание ученика. Общий повтор для всех эндпоинтов не
    делаем: там, где транзакция уже успела отправить что-то наружу, повтор
    задваивает отправку — здесь наружу не уходит ничего.

    Факт повтора пишется в лог с именем класса исключения: разбор аварий идёт
    грепом `DeadlockDetected` по `logs/app.log`, и пережитая взаимоблокировка
    обязана быть в нём видна так же, как упавшая.
    """
    for attempt in (1, 2):
        try:
            return await learning_service.resolve_next_item(
                db,
                student_id,
                root_course_id=root_course_id,
                after_material_id=after_material_id,
                after_task_id=after_task_id,
            )
        except DBAPIError as exc:
            if attempt == 2 or not is_deadlock_error(exc):
                raise
            logger.warning(
                "next-item: %s (student_id=%s root_course_id=%s) — транзакция снята "
                "базой, повторяем с чистого листа",
                type(exc.orig).__name__, student_id, root_course_id,
            )
            await db.rollback()
    raise AssertionError("недостижимо: цикл повторов всегда возвращает или бросает")

@router.get(
    "/next-item",
    response_model=NextItemResponse,
    responses=_PAYMENT_403,
    summary="Следующий шаг для студента (material | task | none | blocked_*)",
)
async def get_next_item(
    student_id: int = Query(..., description="ID студента"),
    root_course_id: int | None = Query(
        None,
        description="Необязательный фильтр: ограничить обход деревом этого корневого "
        "курса. Если не задан — обход всех активных курсов (tsk-127).",
    ),
    after_material_id: int | None = Query(
        None,
        description="Текущая позиция ученика — материал: искать следующий шаг строго "
        "ПОСЛЕ него по порядку обхода курса (tsk-261). Без позиции — первый "
        "незавершённый элемент с начала дерева (прежнее поведение).",
    ),
    after_task_id: int | None = Query(
        None,
        description="Текущая позиция ученика — задание: искать следующий шаг строго "
        "ПОСЛЕ него по порядку обхода курса (tsk-261).",
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> NextItemResponse:
    if not current_user.is_service and current_user.id != student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")

    # tsk-010/tsk-617: просроченная оплата закрывает учебный контент. Гейт по
    # УЧЕНИКУ, а не по вызывающему: боты ходят по сервисному ключу, и пока он
    # освобождал от проверки, блокировка снималась сменой клиента — в браузере
    # закрыто, в Telegram открыто (класс tsk-433).
    await payment_access_service.assert_content_allowed(db, student_id)
    user = await users_service.get_by_id(db, student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    result = await _resolve_next_item_with_retry(
        db,
        student_id,
        root_course_id=root_course_id,
        after_material_id=after_material_id,
        after_task_id=after_task_id,
    )
    if result.type == "blocked_limit" and result.task_id is not None:
        state = await learning_service.compute_task_state(
            db, student_id, result.task_id, root_course_id=result.root_course_id
        )
        await get_or_create_blocked_limit_help_request(
            db,
            student_id=student_id,
            task_id=result.task_id,
            course_id=result.course_id,
            attempt_id=state.last_attempt_id,
            attempts_used=state.attempts_used,
            attempts_limit_effective=state.attempts_limit_effective,
            last_based_status=state.state,
        )
    if result.type in ("blocked_dependency", "blocked_limit"):
        logger.warning(
            "next-item: student_id=%s type=%s course_id=%s",
            student_id, result.type, result.course_id,
        )
    else:
        logger.info(
            "next-item: student_id=%s type=%s course_id=%s material_id=%s task_id=%s",
            student_id, result.type, result.course_id, result.material_id, result.task_id,
        )
    await db.commit()
    return NextItemResponse(
        type=result.type,
        course_id=result.course_id,
        root_course_id=result.root_course_id,
        material_id=result.material_id,
        task_id=result.task_id,
        reason=result.reason,
        dependency_course_id=result.dependency_course_id,
        dependency_course_title=result.dependency_course_title,
        dependency_course_uid=result.dependency_course_uid,
    )


# ----- POST /learning/materials/{material_id}/complete -----

@router.post(
    "/materials/{material_id}/complete",
    response_model=MaterialCompleteResponse,
    responses=_PAYMENT_403,
    summary="Отметить материал как пройденный (идемпотентно)",
)
async def material_complete(
    material_id: int = Path(..., description="ID материала"),
    body: MaterialCompleteRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> MaterialCompleteResponse:
    if not current_user.is_service and current_user.id != body.student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")

    # tsk-010/tsk-617: просроченная оплата закрывает учебный контент. Гейт по
    # УЧЕНИКУ, а не по вызывающему: боты ходят по сервисному ключу, и пока он
    # освобождал от проверки, блокировка снималась сменой клиента — в браузере
    # закрыто, в Telegram открыто (класс tsk-433).
    await payment_access_service.assert_content_allowed(db, body.student_id)
    material = await materials_service.get_by_id(db, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Материал не найден")
    user = await users_service.get_by_id(db, body.student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    completed_at = await set_material_completed(db, body.student_id, material_id)
    await db.commit()
    # tsk-439: реальное учебное действие во время окна занятия подтверждает
    # явку автоматически. Soft-fail — явка не должна ломать учебный поток.
    try:
        await lesson_attendance_service.auto_confirm_if_in_progress(
            db, student_id=body.student_id,
        )
    except Exception:
        logger.warning(
            "tsk-439: auto-confirm attendance failed: student_id=%s", body.student_id, exc_info=True,
        )
        try:
            await db.rollback()
        except Exception:
            pass
    logger.info("material complete: student_id=%s material_id=%s", body.student_id, material_id)
    return MaterialCompleteResponse(
        ok=True,
        student_id=body.student_id,
        material_id=material_id,
        status="completed",
        completed_at=completed_at,
    )


# ----- POST /learning/materials/{material_id}/skip -----

@router.post(
    "/materials/{material_id}/skip",
    response_model=LearningSkipResponse,
    responses=_PAYMENT_403,
    summary="Пропустить skippable-материал (идемпотентно)",
)
async def material_skip(
    material_id: int = Path(..., description="ID материала"),
    body: LearningSkipRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> LearningSkipResponse:
    if not current_user.is_service and current_user.id != body.student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")

    # tsk-010/tsk-617: просроченная оплата закрывает учебный контент. Гейт по
    # УЧЕНИКУ, а не по вызывающему: боты ходят по сервисному ключу, и пока он
    # освобождал от проверки, блокировка снималась сменой клиента — в браузере
    # закрыто, в Telegram открыто (класс tsk-433).
    await payment_access_service.assert_content_allowed(db, body.student_id)
    material = await materials_service.get_by_id(db, material_id)
    if material is None or not material.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Материал не найден")
    if material.requirement_level != "skippable":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="material_not_skippable",
        )
    user = await users_service.get_by_id(db, body.student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    progress_status, skipped_at = await set_material_skipped(db, body.student_id, material_id)
    if progress_status == "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="already_completed",
        )
    await db.commit()
    # tsk-439: реальное учебное действие во время окна занятия подтверждает
    # явку автоматически. Soft-fail — явка не должна ломать учебный поток.
    try:
        await lesson_attendance_service.auto_confirm_if_in_progress(
            db, student_id=body.student_id,
        )
    except Exception:
        logger.warning(
            "tsk-439: auto-confirm attendance failed: student_id=%s", body.student_id, exc_info=True,
        )
        try:
            await db.rollback()
        except Exception:
            pass
    logger.info("material skip: student_id=%s material_id=%s", body.student_id, material_id)
    return LearningSkipResponse(
        ok=True,
        student_id=body.student_id,
        kind="material",
        material_id=material_id,
        status="skipped",
        skipped_at=skipped_at,
    )


# ----- POST /learning/tasks/{task_id}/start-or-get-attempt -----

@router.post(
    "/tasks/{task_id}/skip",
    response_model=LearningSkipResponse,
    responses=_PAYMENT_403,
    summary="Пропустить skippable-задание (идемпотентно)",
)
async def task_skip(
    task_id: int = Path(..., description="ID задания"),
    body: LearningSkipRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> LearningSkipResponse:
    if not current_user.is_service and current_user.id != body.student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")

    # tsk-010/tsk-617: просроченная оплата закрывает учебный контент. Гейт по
    # УЧЕНИКУ, а не по вызывающему: боты ходят по сервисному ключу, и пока он
    # освобождал от проверки, блокировка снималась сменой клиента — в браузере
    # закрыто, в Telegram открыто (класс tsk-433).
    await payment_access_service.assert_content_allowed(db, body.student_id)
    task = await tasks_service.get_by_id(db, task_id)
    if task is None or not task.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание не найдено")
    if task.requirement_level != "skippable":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="task_not_skippable",
        )
    user = await users_service.get_by_id(db, body.student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    state_result = await learning_service.compute_task_state(db, body.student_id, task_id)
    if state_result.state == "PASSED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="already_completed",
        )
    skipped_at = await set_task_skipped(db, body.student_id, task_id)
    await db.commit()
    logger.info("task skip: student_id=%s task_id=%s", body.student_id, task_id)
    return LearningSkipResponse(
        ok=True,
        student_id=body.student_id,
        kind="task",
        task_id=task_id,
        status="skipped",
        skipped_at=skipped_at,
    )

@router.post(
    "/tasks/{task_id}/start-or-get-attempt",
    response_model=StartOrGetAttemptResponse,
    responses=_PAYMENT_403,
    summary="Начать попытку или вернуть текущую незавершённую (идемпотентно)",
)
async def start_or_get_attempt(
    task_id: int = Path(..., description="ID задания"),
    body: StartOrGetAttemptRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> StartOrGetAttemptResponse:
    if not current_user.is_service and current_user.id != body.student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")

    # tsk-010/tsk-617: просроченная оплата закрывает учебный контент. Гейт по
    # УЧЕНИКУ, а не по вызывающему: боты ходят по сервисному ключу, и пока он
    # освобождал от проверки, блокировка снималась сменой клиента — в браузере
    # закрыто, в Telegram открыто (класс tsk-433).
    await payment_access_service.assert_content_allowed(db, body.student_id)
    # tsk-673: тариф «Выпускник» закрывает работу в курсе — попытку не начать.
    # Материалы и чтение самого задания при этом остаются открытыми: закрыта
    # сдача, а не курс (решение оператора 2026-08-25).
    await graduation_service.assert_course_work_allowed(db, body.student_id)
    task = await tasks_service.get_by_id(db, task_id)
    if task is None or not task.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание не найдено")
    user = await users_service.get_by_id(db, body.student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    course_id = task.course_id

    # tsk-264: курс САМОГО задания (course_id) путь не различает — узел
    # переиспользуется несколькими курсами, и для него course_id одинаков при
    # любом пути. Контекст навигации (корень) фиксируем в попытке: по нему потом
    # считается лимит. Заявленный клиентом корень проверяется на то, что его
    # дерево действительно содержит узел.
    try:
        root_course_id = await learning_service.resolve_attempt_root(
            db,
            student_id=body.student_id,
            course_id=course_id,
            requested_root_course_id=body.root_course_id,
        )
    except DomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    # Concurrency-safe: один активный attempt на (user_id, course_id, root_course_id).
    # Advisory lock сериализует параллельные запросы для этой тройки: корень входит
    # в ключ, иначе попытка из курса X переиспользовалась бы в курсе Y и её
    # результаты записались бы в чужой контекст.
    # hashtext, а не арифметика по id: произведение id курса на множитель
    # переполняет int4, который принимает pg_advisory_xact_lock.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:k1, hashtext(:k2))"),
        {"k1": body.student_id, "k2": f"{course_id}:{root_course_id}"},
    )

    # Активная попытка по этому курсу и корню (не завершена и не отменена)
    stmt = (
        select(Attempts)
        .where(
            Attempts.user_id == body.student_id,
            Attempts.course_id == course_id,
            Attempts.root_course_id.is_(None)
            if root_course_id is None
            else Attempts.root_course_id == root_course_id,
            Attempts.finished_at.is_(None),
            Attempts.cancelled_at.is_(None),
        )
        .order_by(Attempts.created_at.desc())
        .limit(1)
    )
    r = await db.execute(stmt)
    existing = r.scalar_one_or_none()
    if existing is not None:
        existing = await attempts_service.ensure_attempt_task_ids(db, existing, task_id)
        # tsk-578: телеметрия открытия — тот же вызов ученик делает при КАЖДОМ
        # заходе на страницу задания (см. record_task_opened), включая повторное
        # открытие в рамках уже начатой попытки.
        await record_task_opened(
            db, student_id=body.student_id, task_id=task_id,
            attempt_id=existing.id, is_new_attempt=False,
        )
        await db.commit()
        return StartOrGetAttemptResponse(
            attempt_id=existing.id,
            user_id=existing.user_id,
            course_id=existing.course_id,
            root_course_id=existing.root_course_id,
            created_at=existing.created_at,
            finished_at=existing.finished_at,
            source_system=existing.source_system,
        )

    attempt = await attempts_service.create_attempt(
        db=db,
        user_id=body.student_id,
        course_id=course_id,
        root_course_id=root_course_id,
        source_system=body.source_system or "learning_api",
        meta={"task_ids": [task_id]},
    )
    attempt = await attempts_service.ensure_attempt_task_ids(db, attempt, task_id)
    # tsk-578: первое открытие задания в новой попытке.
    await record_task_opened(
        db, student_id=body.student_id, task_id=task_id,
        attempt_id=attempt.id, is_new_attempt=True,
    )
    await db.commit()
    logger.info(
        "start-or-get-attempt: student_id=%s task_id=%s attempt_id=%s root_course_id=%s",
        body.student_id, task_id, attempt.id, root_course_id,
    )
    return StartOrGetAttemptResponse(
        attempt_id=attempt.id,
        user_id=attempt.user_id,
        course_id=attempt.course_id,
        root_course_id=attempt.root_course_id,
        created_at=attempt.created_at,
        finished_at=attempt.finished_at,
        source_system=attempt.source_system,
    )


# ----- GET /learning/tasks/{task_id}/state -----

@router.get(
    "/tasks/{task_id}/state",
    response_model=TaskStateResponse,
    responses=_PAYMENT_403,
    summary="Состояние задания по последней завершённой попытке",
)
async def get_task_state(
    task_id: int = Path(..., description="ID задания"),
    student_id: int = Query(..., description="ID студента"),
    root_course_id: int | None = Query(
        None,
        description="Корневой курс, которым ученик пришёл к заданию (tsk-264): "
        "попытки считаются в его границах. Не задан — счёт по всем попыткам "
        "задания независимо от пути (прежнее поведение).",
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> TaskStateResponse:
    if not current_user.is_service and current_user.id != student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")

    # tsk-010/tsk-617: просроченная оплата закрывает учебный контент. Гейт по
    # УЧЕНИКУ, а не по вызывающему: боты ходят по сервисному ключу, и пока он
    # освобождал от проверки, блокировка снималась сменой клиента — в браузере
    # закрыто, в Telegram открыто (класс tsk-433).
    await payment_access_service.assert_content_allowed(db, student_id)
    task = await tasks_service.get_by_id(db, task_id)
    if task is None or not task.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание не найдено")
    user = await users_service.get_by_id(db, student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    # tsk-264: тот же резолвер, что у start-or-get-attempt — состояние задания и
    # счёт при открытии попытки обязаны сходиться, иначе SPW покажет одно, а
    # сервер применит другое.
    try:
        effective_root_id = await learning_service.resolve_attempt_root(
            db,
            student_id=student_id,
            course_id=task.course_id,
            requested_root_course_id=root_course_id,
        )
    except DomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    state = await learning_service.compute_task_state(
        db, student_id, task_id, root_course_id=effective_root_id
    )
    # tsk-227: проброс флага обязательного вложения клиенту (UX-сигнал; форс — на сервере).
    # tsk-547: рядом — есть ли эталон для сверки `value`. Считается ТИПО-ЗАВИСИМО:
    # у SC/MC/TA/квизов блок `short_answer` не заполняется в принципе, и «эталона
    # нет» там значило бы не то же самое, что у короткого ответа, — клиент принял бы
    # это за «поле ответа не нужно». Для них флаг всегда true.
    try:
        rules = SolutionRules.model_validate(task.solution_rules or {})
        requires_attachment = bool(rules.requires_attachment)
        partial_auto_check = bool(rules.partial_auto_check)
        task_type = (task.task_content or {}).get("type") if isinstance(task.task_content, dict) else None
        has_reference_answer = (
            rules.has_reference_answer()
            if task_type in SHORT_ANSWER_TASK_TYPES
            else True
        )
    except Exception:
        # Некорректные solution_rules не должны ломать выдачу состояния задания.
        requires_attachment = False
        partial_auto_check = False
        has_reference_answer = True
    if state.state == "BLOCKED_LIMIT":
        await get_or_create_blocked_limit_help_request(
            db,
            student_id=student_id,
            task_id=task_id,
            course_id=task.course_id,
            attempt_id=state.last_attempt_id,
            attempts_used=state.attempts_used,
            attempts_limit_effective=state.attempts_limit_effective,
            last_based_status=state.state,
        )
        await db.commit()
    return TaskStateResponse(
        task_id=task_id,
        student_id=student_id,
        state=state.state,
        last_attempt_id=state.last_attempt_id,
        last_score=state.last_score,
        last_max_score=state.last_max_score,
        last_finished_at=state.last_finished_at,
        attempts_used=state.attempts_used,
        attempts_limit_effective=state.attempts_limit_effective,
        last_answer_json=state.last_answer_json,
        last_is_correct=state.last_is_correct,
        last_checked_at=state.last_checked_at,
        requires_attachment=requires_attachment,
        partial_auto_check=partial_auto_check,
        has_reference_answer=has_reference_answer,
    )


# ----- POST /learning/tasks/{task_id}/request-help -----

@router.post(
    "/tasks/{task_id}/request-help",
    response_model=RequestHelpResponse,
    responses={
        403: {
            "description": (
                "tsk-301: разбор с преподавателем не входит в тариф. Тело содержит "
                "`payload.upgrade_hint` — что даёт апгрейд. Авто-заявка при "
                "исчерпании попыток этим гейтом не закрывается. "
                + _PAYMENT_403_DESCRIPTION
            )
        }
    },
    summary="Запросить помощь по заданию (идемпотентно в окне дедупа)",
)
async def request_help(
    task_id: int = Path(..., description="ID задания"),
    body: RequestHelpRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> RequestHelpResponse:
    if not current_user.is_service and current_user.id != body.student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")

    # tsk-010/tsk-617: просроченная оплата закрывает учебный контент. Гейт по
    # УЧЕНИКУ, а не по вызывающему: боты ходят по сервисному ключу, и пока он
    # освобождал от проверки, блокировка снималась сменой клиента — в браузере
    # закрыто, в Telegram открыто (класс tsk-433).
    await payment_access_service.assert_content_allowed(db, body.student_id)

    # tsk-301: эскалация преподавателю входит не во все тарифы. Гейт стоит на
    # РУЧНОМ запросе; авто-заявку `blocked_limit` он не трогает — ученик,
    # упёршийся в лимит попыток, иначе остался бы вовсе без выхода.
    #
    # Проверяем по УЧЕНИКУ, а не по вызывающему: в отличие от гейта оплаты выше,
    # сервисный ключ здесь не освобождает. Бот — транспорт, а не отдельное право;
    # иначе вышло бы «через бота можно, через браузер нельзя» (класс tsk-433).
    escalation_gate = await entitlements_service.check(
        db, student_id=body.student_id, capability="teacher_escalation"
    )
    if entitlements_service.should_block(
        escalation_gate,
        capability="teacher_escalation",
        student_id=body.student_id,
    ):
        raise DomainError(
            detail=(
                escalation_gate.upgrade_hint
                or "Разбор с преподавателем не входит в ваш тариф."
            ),
            status_code=403,
            payload={
                "code": "subscription_denied",
                "outcome": escalation_gate.outcome,
                "upgrade_hint": escalation_gate.upgrade_hint,
            },
        )

    task = await tasks_service.get_by_id(db, task_id)
    if task is None or not task.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание не найдено")
    user = await users_service.get_by_id(db, body.student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    event_id, deduplicated = await record_help_requested(
        db, body.student_id, task_id, body.message
    )
    request_id, _ = await get_or_create_help_request(
        db,
        student_id=body.student_id,
        task_id=task_id,
        event_id=event_id,
        message=body.message,
        course_id=task.course_id,
        deduplicated=deduplicated,
    )
    await db.commit()
    logger.info(
        "request-help: student_id=%s task_id=%s event_id=%s deduplicated=%s request_id=%s",
        body.student_id, task_id, event_id, deduplicated, request_id,
    )
    return RequestHelpResponse(
        ok=True, event_id=event_id, deduplicated=deduplicated, request_id=request_id
    )


# ----- tsk-303: лестница помощи, сторона ученика -----

# Состояние заявки не сошлось с запрошенным шагом лестницы. Для клиента это
# один класс: экран устарел, надо перечитать заявку.
_LADDER_STATE_ERRORS: dict[str, str] = {
    "not_closed": "Заявка ещё открыта — возвращать нечего.",
    "not_open": "Заявка закрыта.",
    "wrong_type": "Это действие недоступно для заявки такого типа.",
    "no_reopen": "Индивидуальный разбор доступен после повторного обращения по этому заданию.",
    "no_webinar_link": "Преподаватель ещё не прислал ссылку на разбор.",
    "already_rated": "Разбор уже оценён.",
}


def _raise_ladder_error(err: str) -> None:
    """Единая раскладка ошибок шагов лестницы в HTTP-коды."""
    if err == "not_found":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    if err == "forbidden":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к заявке")
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        _LADDER_STATE_ERRORS.get(err, "Действие недоступно в текущем состоянии заявки."),
    )


async def _resolve_ladder_student(
    db: AsyncSession,
    request_id: int,
    current_user: CurrentUser,
) -> int:
    """От имени какого ученика выполняется шаг лестницы.

    Обычный пользователь действует только от себя — сервис ниже сверит это с
    владельцем заявки и отдаст 403 при чужом `request_id`. Сервисный ключ
    (боты) действует от имени владельца, как и на остальных learning-путях,
    поэтому владельца берём из самой заявки.

    Ключевое: `student_id` нигде не приходит из тела/запроса — подставить
    чужой нечем, а значит перебором `request_id` чужую заявку не прочитать.
    """
    if not current_user.is_service:
        return current_user.id
    row = (
        await db.execute(
            text("SELECT student_id FROM help_requests WHERE id = :id"),
            {"id": request_id},
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    return int(row[0])


@router.get(
    "/tasks/{task_id}/help-request",
    response_model=Optional[StudentHelpRequestResponse],
    summary="Текущая заявка помощи ученика по заданию (tsk-303)",
    responses={
        200: {"description": "Заявка или null, если ученик помощь не запрашивал"},
        403: {"description": "Чужой student_id"},
    },
)
async def student_help_request_state(
    task_id: int = Path(..., description="ID задания"),
    student_id: int = Query(..., description="ID ученика"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> Optional[StudentHelpRequestResponse]:
    """Состояние лестницы помощи для страницы задания.

    До tsk-303 ученик вообще не мог увидеть на странице задания, что с его
    заявкой: ответ учителя долетал только уведомлением в общей ленте, а сама
    страница о заявке не знала. Без этого чтения кнопкам «Вернуть заявку» и
    «Запросить разбор» негде появиться.

    Гейт оплаты здесь НЕ применяется: читать состояние собственного обращения
    должник вправе — закрыт учебный контент, а не поддержка.
    """
    if not current_user.is_service and current_user.id != student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    data = await get_student_help_request(db, student_id, task_id)
    if data is None:
        return None
    return StudentHelpRequestResponse(**data)


@router.post(
    "/help-requests/{request_id}/reopen",
    response_model=HelpRequestReopenResponse,
    summary="Вернуть заявку: ответ преподавателя не помог (tsk-303)",
    responses={
        404: {"description": "Заявка не найдена"},
        403: {"description": "Чужая заявка. " + _PAYMENT_403_DESCRIPTION},
        409: {"description": "Заявка не закрыта или не того типа"},
    },
)
async def reopen_student_help_request(
    request_id: int = Path(..., description="ID заявки"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> HelpRequestReopenResponse:
    """Уровень 1 → повтор: ученик отмечает, что после ответа не разобрался.

    Возврат начисляется преподавателю, чей ответ не помог, — это KPI
    (`help_request_reopens`). Владелец заявки берётся из самой заявки, а не из
    тела запроса: подставить чужой `student_id` тут физически нечем.
    """
    student_id = await _resolve_ladder_student(db, request_id, current_user)
    # tsk-617: гейт по владельцу заявки, а не по вызывающему — см. next-item.
    await payment_access_service.assert_content_allowed(db, student_id)
    data, err = await reopen_help_request(db, request_id, student_id)
    if err is not None:
        _raise_ladder_error(err)
    await db.commit()
    logger.info("tsk-303 reopen: request_id=%s student_id=%s", request_id, student_id)
    return HelpRequestReopenResponse(**data)


@router.post(
    "/help-requests/{request_id}/request-individual-review",
    response_model=IndividualReviewResponse,
    summary="Запросить индивидуальный разбор (tsk-303, уровень 2)",
    responses={
        404: {"description": "Заявка не найдена"},
        403: {"description": "Чужая заявка. " + _PAYMENT_403_DESCRIPTION},
        409: {"description": "Заявка закрыта, не того типа или не возвращалась"},
    },
)
async def request_individual_review_endpoint(
    request_id: int = Path(..., description="ID заявки"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> IndividualReviewResponse:
    """Доступно только по заявке, которую ученик уже возвращал."""
    student_id = await _resolve_ladder_student(db, request_id, current_user)
    # tsk-617: гейт по владельцу заявки, а не по вызывающему — см. next-item.
    await payment_access_service.assert_content_allowed(db, student_id)
    data, err = await request_individual_review(db, request_id, student_id)
    if err is not None:
        _raise_ladder_error(err)
    await db.commit()
    logger.info(
        "tsk-303 individual-review: request_id=%s student_id=%s already=%s",
        request_id, student_id, data.get("already"),
    )
    return IndividualReviewResponse(**data)


@router.post(
    "/help-requests/{request_id}/rate-review",
    response_model=RateReviewResponse,
    summary="Оценить индивидуальный разбор (tsk-303, уровень 3)",
    responses={
        404: {"description": "Заявка не найдена"},
        # Гейта оплаты здесь нет намеренно — см. docstring обработчика.
        403: {"description": "Чужая заявка"},
        409: {"description": "Нет ссылки на разбор, заявка закрыта или уже оценена"},
    },
)
async def rate_individual_review_endpoint(
    request_id: int = Path(..., description="ID заявки"),
    body: RateReviewRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> RateReviewResponse:
    """«Понятно» закрывает заявку, «непонятно» уводит её методисту.

    Гейт оплаты здесь НЕ применяется намеренно: это ответ на уже оказанную
    помощь. Заблокировать его значило бы оставить заявку висеть открытой без
    оценки и без маршрута дальше.
    """
    student_id = await _resolve_ladder_student(db, request_id, current_user)
    data, err = await rate_individual_review(db, request_id, student_id, body.understood)
    if err is not None:
        _raise_ladder_error(err)
    await db.commit()
    logger.info(
        "tsk-303 rate-review: request_id=%s student_id=%s understood=%s escalated=%s",
        request_id, student_id, data.get("understood"), data.get("escalated"),
    )
    return RateReviewResponse(**data)


# ----- POST /learning/tasks/{task_id}/hint-events (этап 3.6) -----

@router.post(
    "/tasks/{task_id}/hint-events",
    response_model=HintEventResponse,
    status_code=status.HTTP_200_OK,
    summary="Зафиксировать открытие подсказки (телеметрия, идемпотентно)",
    responses={
        200: {"description": "Событие записано или дедуплицировано"},
        403: {"description": _PAYMENT_403_DESCRIPTION},
        404: {"description": "Задание / студент / попытка не найдены"},
        409: {"description": "attempt не принадлежит student_id; попытка завершена/отменена; или задание не в контексте попытки"},
    },
)
async def hint_events(
    task_id: int = Path(..., description="ID задания"),
    body: HintEventRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> HintEventResponse:
    if not current_user.is_service and current_user.id != body.student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")

    # tsk-010/tsk-617: просроченная оплата закрывает учебный контент. Гейт по
    # УЧЕНИКУ, а не по вызывающему: боты ходят по сервисному ключу, и пока он
    # освобождал от проверки, блокировка снималась сменой клиента — в браузере
    # закрыто, в Telegram открыто (класс tsk-433).
    await payment_access_service.assert_content_allowed(db, body.student_id)
    """
    Фиксация открытия подсказки (text/video) для аналитики. Идемпотентно в окне дедупа.
    """
    task = await tasks_service.get_by_id(db, task_id)
    if task is None or not task.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание не найдено")
    user = await users_service.get_by_id(db, body.student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    attempt = await attempts_service.get_by_id(db, body.attempt_id)
    if attempt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Попытка не найдена")

    if attempt.user_id != body.student_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Попытка не принадлежит указанному студенту",
        )
    if attempt.finished_at is not None or attempt.cancelled_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Попытка уже завершена или отменена. События подсказок принимаются только для активной попытки.",
        )
    if attempt.course_id != task.course_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Попытка не соответствует курсу задания",
        )
    meta = attempt.meta or {}
    task_ids = meta.get("task_ids") if isinstance(meta, dict) else None
    if isinstance(task_ids, list) and len(task_ids) > 0 and task_id not in task_ids:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Задание не входит в контекст попытки",
        )

    event_id, deduplicated = await record_hint_open(
        db,
        student_id=body.student_id,
        attempt_id=body.attempt_id,
        task_id=task_id,
        hint_type=body.hint_type,
        hint_index=body.hint_index,
        action=body.action,
        source=body.source,
    )
    await db.commit()
    logger.info(
        "hint-events: task_id=%s attempt_id=%s hint_type=%s hint_index=%s event_id=%s deduplicated=%s",
        task_id, body.attempt_id, body.hint_type, body.hint_index, event_id, deduplicated,
    )
    return HintEventResponse(ok=True, deduplicated=deduplicated, event_id=event_id)
