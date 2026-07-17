"""
Learning API V1 (этап 3): next-item, materials/complete, tasks/start-or-get-attempt, state, request-help.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Path, Body, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, get_current_user
from app.auth.current_user import CurrentUser
from app.models.attempts import Attempts
from app.models.tasks import Tasks
from app.schemas.solution_rules import SolutionRules
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
    HintEventRequest,
    HintEventResponse,
)
from app.services.learning_engine_service import LearningEngineService
from app.services.learning_events_service import (
    record_help_requested,
    record_hint_open,
    set_material_completed,
    set_material_skipped,
    set_task_skipped,
)
from app.services.help_requests_service import (
    get_or_create_help_request,
    get_or_create_blocked_limit_help_request,
)
from app.services.attempts_service import AttemptsService
from app.services.tasks_service import TasksService
from app.services.materials_service import MaterialsService
from app.services.users_service import UsersService

router = APIRouter(prefix="/learning", tags=["learning"])
logger = logging.getLogger("api.learning")

learning_service = LearningEngineService()
attempts_service = AttemptsService()
tasks_service = TasksService()
materials_service = MaterialsService()
users_service = UsersService()


# ----- GET /learning/next-item -----
# Внимание: GET выполняет запись в БД (upsert student_course_state при проверке зависимостей).
# Это создаёт write-амплификацию при частых вызовах; для read-only сценариев можно вынести
# обновление состояния в отдельный вызов или кэш.

@router.get(
    "/next-item",
    response_model=NextItemResponse,
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
    user = await users_service.get_by_id(db, student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    result = await learning_service.resolve_next_item(
        db,
        student_id,
        root_course_id=root_course_id,
        after_material_id=after_material_id,
        after_task_id=after_task_id,
    )
    if result.type == "blocked_limit" and result.task_id is not None:
        state = await learning_service.compute_task_state(db, student_id, result.task_id)
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
    )


# ----- POST /learning/materials/{material_id}/complete -----

@router.post(
    "/materials/{material_id}/complete",
    response_model=MaterialCompleteResponse,
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
    material = await materials_service.get_by_id(db, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Материал не найден")
    user = await users_service.get_by_id(db, body.student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    completed_at = await set_material_completed(db, body.student_id, material_id)
    await db.commit()
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
    summary="РџСЂРѕРїСѓСЃС‚РёС‚СЊ skippable-РјР°С‚РµСЂРёР°Р» (РёРґРµРјРїРѕС‚РµРЅС‚РЅРѕ)",
)
async def material_skip(
    material_id: int = Path(..., description="ID РјР°С‚РµСЂРёР°Р»Р°"),
    body: LearningSkipRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> LearningSkipResponse:
    if not current_user.is_service and current_user.id != body.student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    material = await materials_service.get_by_id(db, material_id)
    if material is None or not material.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="РњР°С‚РµСЂРёР°Р» РЅРµ РЅР°Р№РґРµРЅ")
    if material.requirement_level != "skippable":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="material_not_skippable",
        )
    user = await users_service.get_by_id(db, body.student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="РЎС‚СѓРґРµРЅС‚ РЅРµ РЅР°Р№РґРµРЅ")
    progress_status, skipped_at = await set_material_skipped(db, body.student_id, material_id)
    if progress_status == "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="already_completed",
        )
    await db.commit()
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
    summary="РџСЂРѕРїСѓСЃС‚РёС‚СЊ skippable-Р·Р°РґР°РЅРёРµ (РёРґРµРјРїРѕС‚РµРЅС‚РЅРѕ)",
)
async def task_skip(
    task_id: int = Path(..., description="ID Р·Р°РґР°РЅРёСЏ"),
    body: LearningSkipRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> LearningSkipResponse:
    if not current_user.is_service and current_user.id != body.student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    task = await tasks_service.get_by_id(db, task_id)
    if task is None or not task.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Р—Р°РґР°РЅРёРµ РЅРµ РЅР°Р№РґРµРЅРѕ")
    if task.requirement_level != "skippable":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="task_not_skippable",
        )
    user = await users_service.get_by_id(db, body.student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="РЎС‚СѓРґРµРЅС‚ РЅРµ РЅР°Р№РґРµРЅ")
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
    task = await tasks_service.get_by_id(db, task_id)
    if task is None or not task.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание не найдено")
    user = await users_service.get_by_id(db, body.student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    course_id = task.course_id

    # Concurrency-safe: один активный attempt на (user_id, course_id).
    # Advisory lock сериализует параллельные запросы для этой пары.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
        {"k1": body.student_id, "k2": course_id},
    )

    # Активная попытка по этому курсу (не завершена и не отменена)
    stmt = (
        select(Attempts)
        .where(
            Attempts.user_id == body.student_id,
            Attempts.course_id == course_id,
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
        await db.commit()
        return StartOrGetAttemptResponse(
            attempt_id=existing.id,
            user_id=existing.user_id,
            course_id=existing.course_id,
            created_at=existing.created_at,
            finished_at=existing.finished_at,
            source_system=existing.source_system,
        )

    attempt = await attempts_service.create_attempt(
        db=db,
        user_id=body.student_id,
        course_id=course_id,
        source_system=body.source_system or "learning_api",
        meta={"task_ids": [task_id]},
    )
    attempt = await attempts_service.ensure_attempt_task_ids(db, attempt, task_id)
    await db.commit()
    logger.info(
        "start-or-get-attempt: student_id=%s task_id=%s attempt_id=%s",
        body.student_id, task_id, attempt.id,
    )
    return StartOrGetAttemptResponse(
        attempt_id=attempt.id,
        user_id=attempt.user_id,
        course_id=attempt.course_id,
        created_at=attempt.created_at,
        finished_at=attempt.finished_at,
        source_system=attempt.source_system,
    )


# ----- GET /learning/tasks/{task_id}/state -----

@router.get(
    "/tasks/{task_id}/state",
    response_model=TaskStateResponse,
    summary="Состояние задания по последней завершённой попытке",
)
async def get_task_state(
    task_id: int = Path(..., description="ID задания"),
    student_id: int = Query(..., description="ID студента"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> TaskStateResponse:
    if not current_user.is_service and current_user.id != student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    task = await tasks_service.get_by_id(db, task_id)
    if task is None or not task.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание не найдено")
    user = await users_service.get_by_id(db, student_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Студент не найден")
    state = await learning_service.compute_task_state(db, student_id, task_id)
    # tsk-227: проброс флага обязательного вложения клиенту (UX-сигнал; форс — на сервере).
    try:
        requires_attachment = bool(
            SolutionRules.model_validate(task.solution_rules or {}).requires_attachment
        )
    except Exception:
        # Некорректные solution_rules не должны ломать выдачу состояния задания.
        requires_attachment = False
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
    )


# ----- POST /learning/tasks/{task_id}/request-help -----

@router.post(
    "/tasks/{task_id}/request-help",
    response_model=RequestHelpResponse,
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


# ----- POST /learning/tasks/{task_id}/hint-events (этап 3.6) -----

@router.post(
    "/tasks/{task_id}/hint-events",
    response_model=HintEventResponse,
    status_code=status.HTTP_200_OK,
    summary="Зафиксировать открытие подсказки (телеметрия, идемпотентно)",
    responses={
        200: {"description": "Событие записано или дедуплицировано"},
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
