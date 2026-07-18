"""
Teacher reviews API (Learning Engine V1, этап 3.9 + Phase Y-4):
- POST /api/v1/teacher/reviews/claim-next      — взять следующий результат
- POST /api/v1/teacher/reviews/{id}/claim      — взять на оценку конкретную работу (tsk-247)
- POST /api/v1/teacher/reviews/{id}/release    — освободить блокировку
- POST /api/v1/teacher/reviews/{id}/grade      — выставить оценку (Y-4)
- GET  /api/v1/teacher/reviews/pending-count   — количество pending без захвата (Y-4)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, get_current_user
from app.auth.current_user import CurrentUser
from app.core.config import Settings
from app.schemas.teacher_next_modes import (
    PendingCountResponse,
    PendingReviewItem,
    PendingReviewListResponse,
    ReviewClaimItem,
    ReviewClaimNextRequest,
    ReviewClaimNextResponse,
    ReviewClaimRequest,
    ReviewClaimResponse,
    ReviewGradeRequest,
    ReviewGradeResponse,
    ReviewRegradePartScore,
    ReviewRegradeRequest,
    ReviewRegradeResponse,
    ReviewReleaseRequest,
    ReviewReleaseResponse,
)
from app.services import audit_service, inbox_service, notification_email_service
from app.services.teacher_queue_service import (
    ClaimForbiddenError,
    GradeConflictError,
    GradeNotFoundError,
    GradeValidationError,
    claim_next_review,
    claim_review_by_id,
    get_pending_count,
    grade_review,
    list_pending_reviews,
    regrade_review,
    release_review_claim,
)

router = APIRouter(prefix="/teacher/reviews", tags=["teacher_reviews"])
logger = logging.getLogger("api.teacher_reviews")
_settings = Settings()


@router.post(
    "/claim-next",
    response_model=ReviewClaimNextResponse,
    status_code=status.HTTP_200_OK,
    summary="Взять следующий результат на ручную проверку (атомарный claim)",
    responses={
        200: {"description": "Кейс выдан или empty=true"},
        422: {"description": "Невалидные параметры"},
    },
)
async def review_claim_next(
    body: ReviewClaimNextRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> ReviewClaimNextResponse:
    if not current_user.is_service and current_user.id != body.teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    item, lock_token, lock_expires_at = await claim_next_review(
        db,
        teacher_id=body.teacher_id,
        ttl_sec=body.ttl_sec,
        course_id=body.course_id,
        user_id=body.user_id,
        idempotency_key=body.idempotency_key,
    )
    await db.commit()
    if item is None:
        return ReviewClaimNextResponse(empty=True, item=None, lock_token=None, lock_expires_at=None)
    return ReviewClaimNextResponse(
        empty=False,
        item=ReviewClaimItem(**item),
        lock_token=lock_token,
        lock_expires_at=lock_expires_at,
    )


@router.post(
    "/{result_id}/claim",
    response_model=ReviewClaimResponse,
    status_code=status.HTTP_200_OK,
    summary="Взять на оценку конкретную работу (claim по result_id, tsk-247)",
    responses={
        200: {"description": "Блокировка выдана"},
        403: {"description": "Работа вне зоны ответственности преподавателя"},
        404: {"description": "Работа не найдена или не подлежит ручной оценке"},
        409: {"description": "Уже оценена или захвачена другим преподавателем"},
    },
)
async def review_claim_by_id(
    result_id: int = Path(..., description="ID результата (task_result)"),
    body: ReviewClaimRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> ReviewClaimResponse:
    """Захватить конкретную работу под оценку.

    Дополняет claim-next: тот выдаёт следующую работу из ОБЯЗАТЕЛЬНОЙ очереди,
    а этот позволяет оценить опциональную работу, открытую из списка вручную.
    """
    if not current_user.is_service and current_user.id != body.teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    try:
        item, lock_token, lock_expires_at = await claim_review_by_id(
            db,
            result_id=result_id,
            teacher_id=body.teacher_id,
            ttl_sec=body.ttl_sec,
        )
    except GradeNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, e.message)
    except ClaimForbiddenError as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, e.message)
    except GradeConflictError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, e.message)
    await db.commit()
    logger.info(
        "review_claim_by_id result_id=%s teacher_id=%s ttl=%s",
        result_id, body.teacher_id, body.ttl_sec,
    )
    return ReviewClaimResponse(
        item=ReviewClaimItem(**item),
        lock_token=lock_token,
        lock_expires_at=lock_expires_at,
    )


@router.post(
    "/{result_id}/release",
    response_model=ReviewReleaseResponse,
    status_code=status.HTTP_200_OK,
    summary="Освободить блокировку проверки (этап 3.9)",
    responses={
        200: {"description": "released=true или идемпотентно released=false"},
        404: {"description": "Результат не найден"},
        409: {"description": "Токен не совпал или кейс у другого преподавателя"},
    },
)
async def review_release(
    result_id: int = Path(..., description="ID результата (task_result)"),
    body: ReviewReleaseRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> ReviewReleaseResponse:
    if not current_user.is_service and current_user.id != body.teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    released, err = await release_review_claim(
        db, result_id, body.teacher_id, body.lock_token
    )
    if err == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Результат не найден")
    if err == "forbidden":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Токен блокировки не совпадает или проверка захвачена другим преподавателем",
        )
    await db.commit()
    return ReviewReleaseResponse(released=released)


@router.post(
    "/{result_id}/grade",
    response_model=ReviewGradeResponse,
    status_code=status.HTTP_200_OK,
    summary="Выставить оценку SA_COM-проверке (Phase Y-4)",
    responses={
        200: {"description": "Оценка выставлена; inbox создан; email best-effort"},
        401: {"description": "Не аутентифицирован"},
        403: {"description": "Не teacher или чужой teacher_id"},
        404: {"description": "task_result не существует"},
        409: {"description": "lock_token mismatch / истёк / уже оценено"},
        422: {"description": "score > max_score"},
    },
)
async def review_grade(
    request: Request,
    background_tasks: BackgroundTasks,
    result_id: int = Path(..., description="ID результата (task_result)"),
    body: ReviewGradeRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> ReviewGradeResponse:
    """Выставить оценку SA_COM-проверке.

    Атомарная транзакция: UPDATE task_results + INSERT notifications + 2x audit_event.
    Email отправляется в фоне после commit (best-effort, не валит ответ).
    """
    if not current_user.is_service and current_user.id != body.teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")

    ip = request.client.host if request.client else "unknown"

    try:
        grade_data = await grade_review(
            db,
            result_id=result_id,
            teacher_id=body.teacher_id,
            lock_token=body.lock_token,
            score=body.score,
            comment=body.comment,
        )
    except GradeNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task_result не найден")
    except GradeConflictError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, e.message)
    except GradeValidationError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, e.message)

    student_id: int = grade_data["user_id"]
    task_id: int = grade_data["task_id"]
    score: int = grade_data["score"]
    max_score: int = grade_data["max_score"]
    is_correct_v: bool = grade_data["is_correct"]
    comment_v: Optional[str] = grade_data["comment"]
    task_title: Optional[str] = grade_data["task_title"]

    # Y-6 notification kind: positive grade → 'sa_com_graded' (existing UX),
    # negative → 'task_returned_for_rework' (NEW, student inbox показывает
    # CTA «Решить заново»; SPW Stage 6.4 рендерит).
    notif_kind = "sa_com_graded" if is_correct_v else "task_returned_for_rework"
    notif_title = (
        "Преподаватель оценил вашу попытку"
        if is_correct_v
        else "Учитель не принял ответ — задача возвращена в очередь"
    )

    # Inbox запись (внутри той же транзакции — атомарно с UPDATE)
    inbox_content = _render_inbox_content(score, max_score, comment_v, is_correct_v)
    notification = await inbox_service.create_for_user(
        db,
        user_id=student_id,
        kind=notif_kind,
        title=notif_title,
        content=inbox_content,
        payload={
            "task_id": task_id,
            "attempt_id": grade_data.get("attempt_id"),
            "score": score,
            "max_score": max_score,
            "is_correct": is_correct_v,
            "comment": comment_v,
            "previous_score": None,
        },
        created_by=body.teacher_id,
    )

    # Audit events.
    # Y-6: при negative grade добавляем `teacher.review.rejected` поверх
    # обычного graded — чтобы в audit-стороне можно было гранулярно мониторить
    # частоту отказов / тренд по преподавателю.
    await audit_service.log_event(
        db,
        audit_service.TEACHER_REVIEW_GRADED,
        user_id=body.teacher_id,
        ip=ip,
        details={
            "result_id": result_id,
            "task_id": task_id,
            "score": score,
            "max_score": max_score,
            "is_correct": is_correct_v,
            "comment_length": len(comment_v) if comment_v else 0,
            "derived_via": "review_pass_threshold_ratio",
        },
    )
    if not is_correct_v:
        await audit_service.log_event(
            db,
            audit_service.TEACHER_REVIEW_REJECTED,
            user_id=body.teacher_id,
            ip=ip,
            details={
                "result_id": result_id,
                "task_id": task_id,
                "score": score,
                "max_score": max_score,
                "student_id": student_id,
            },
        )
    await audit_service.log_event(
        db,
        audit_service.STUDENT_NOTIFICATION_CREATED,
        user_id=student_id,
        ip=ip,
        details={
            "notification_id": notification.id,
            "kind": notif_kind,
            "result_id": result_id,
        },
    )

    await db.commit()

    # Email best-effort после commit. Y-6: отправляем только для positive
    # grade — для negative student узнаёт через inbox (SPW push) + TG-бот.
    # Open new session внутри _send_email_after_commit при необходимости
    # (audit email.failed).
    recipient_email = grade_data.get("user_email")
    if recipient_email and is_correct_v:
        background_tasks.add_task(
            _send_email_and_audit_failure,
            recipient_email=recipient_email,
            task_title=task_title,
            score=score,
            max_score=max_score,
            comment=comment_v,
            student_id=student_id,
        )

    return ReviewGradeResponse(
        result_id=result_id,
        task_id=task_id,
        score=score,
        max_score=max_score,
        is_correct=is_correct_v,
        comment=comment_v,
        notification_id=notification.id,
    )


@router.post(
    "/{result_id}/regrade",
    response_model=ReviewRegradeResponse,
    status_code=status.HTTP_200_OK,
    summary="Изменить ранее выставленную оценку (Phase Y-6)",
    responses={
        200: {"description": "Оценка изменена; inbox создан; regrade_history append"},
        401: {"description": "Не аутентифицирован"},
        403: {"description": "Нет права изменять оценку для этого курса"},
        404: {"description": "task_result не существует"},
        409: {"description": "Заявка ещё не оценена (сначала grade)"},
        422: {"description": "score > max_score"},
    },
)
async def review_regrade(
    request: Request,
    result_id: int = Path(..., description="ID результата (task_result)"),
    body: ReviewRegradeRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> ReviewRegradeResponse:
    """Re-grade уже оценённой проверки.

    Y-6 Stage 3. Доступен:
    - service-key (X-API-Key) — bypass;
    - user с role в {admin, methodist} — bypass любой курс;
    - teacher на course-tree (или ancestor) задачи.

    Notification kind:
    - old=TRUE → new=FALSE → 'task_returned_for_rework'
    - old=FALSE → new=TRUE → 'sa_com_graded'
    - same direction → 'sa_com_regraded' (informational)

    Audit: `teacher.review.regraded` (всегда) + `teacher.review.rejected`
    при new_is_correct=FALSE.

    Concurrency: SELECT FOR UPDATE; не idempotent — каждый regrade event
    сохраняется в `metrics.regrade_history` (полный history).
    """
    ip = request.client.host if request.client else "unknown"

    # ACL: service / methodist / admin / teacher на course-tree.
    if not current_user.is_service:
        # Сначала прочитаем task_result → task → course_id для course-level ACL.
        from sqlalchemy import text as _text
        r = await db.execute(
            _text(
                "SELECT t.course_id FROM task_results tr "
                "JOIN tasks t ON t.id = tr.task_id "
                "WHERE tr.id = :rid"
            ),
            {"rid": result_id},
        )
        row = r.fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "task_result не найден")
        course_id_for_acl: int = int(row[0])
        from app.services.teacher_queue_service import teacher_course_acl as _acl_clause
        # Простая проверка: user либо имеет admin/methodist role, либо teacher
        # на course-tree (через teacher_course_acl helper).
        acl_check = await db.execute(
            _text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM user_roles ur JOIN roles r ON r.id=ur.role_id "
                "  WHERE ur.user_id=:uid AND r.name IN ('admin','methodist')"
                ") AS has_role, "
                "EXISTS ("
                f"  SELECT 1 WHERE {_acl_clause(':course_id_param')}"  # nosec B608 — _acl_clause возвращает SQL из закрытого набора литералов модуля; динамические значения только через bind
                ") AS has_teacher_course"
            ),
            {
                "uid": current_user.id,
                "teacher_id": current_user.id,
                "course_id_param": course_id_for_acl,
            },
        )
        acl_row = acl_check.fetchone()
        if not (acl_row and (acl_row[0] or acl_row[1])):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет права изменить оценку")

    try:
        rg_data = await regrade_review(
            db,
            result_id=result_id,
            actor_user_id=current_user.id,
            score=body.score,
            comment=body.comment,
        )
    except GradeNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task_result не найден")
    except GradeConflictError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, e.message)
    except GradeValidationError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, e.message)

    student_id = int(rg_data["user_id"])
    task_id = int(rg_data["task_id"])
    old_score = int(rg_data["old_score"])
    old_is_correct = bool(rg_data["old_is_correct"])
    new_score = int(rg_data["new_score"])
    new_is_correct = bool(rg_data["new_is_correct"])
    max_score = int(rg_data["max_score"])
    comment_v = rg_data["comment"]
    checked_at = rg_data["checked_at"]

    # Notification kind logic.
    if old_is_correct and not new_is_correct:
        notif_kind = "task_returned_for_rework"
        notif_title = "Учитель пере-оценил ответ — задача снова в очереди"
    elif (not old_is_correct) and new_is_correct:
        notif_kind = "sa_com_graded"
        notif_title = "Учитель пере-оценил — ответ принят"
    else:
        notif_kind = "sa_com_regraded"
        notif_title = "Учитель пере-оценил вашу попытку"

    inbox_content = (
        f"Балл изменён: {old_score}/{max_score} → {new_score}/{max_score}."
    )
    if comment_v:
        clipped = comment_v if len(comment_v) <= 200 else comment_v[:200] + "…"
        inbox_content = f"{inbox_content} {clipped}"

    notification = await inbox_service.create_for_user(
        db,
        user_id=student_id,
        kind=notif_kind,
        title=notif_title,
        content=inbox_content,
        payload={
            "task_id": task_id,
            "attempt_id": rg_data.get("attempt_id"),
            "score": new_score,
            "max_score": max_score,
            "is_correct": new_is_correct,
            "previous_score": old_score,
            "previous_is_correct": old_is_correct,
            "comment": comment_v,
        },
        created_by=current_user.id,
    )

    # Audit
    await audit_service.log_event(
        db,
        audit_service.TEACHER_REVIEW_REGRADED,
        user_id=current_user.id,
        ip=ip,
        details={
            "result_id": result_id,
            "task_id": task_id,
            "old_score": old_score,
            "new_score": new_score,
            "old_is_correct": old_is_correct,
            "new_is_correct": new_is_correct,
            "comment_length": len(comment_v) if comment_v else 0,
        },
    )
    if not new_is_correct:
        await audit_service.log_event(
            db,
            audit_service.TEACHER_REVIEW_REJECTED,
            user_id=current_user.id,
            ip=ip,
            details={
                "result_id": result_id,
                "task_id": task_id,
                "score": new_score,
                "max_score": max_score,
                "student_id": student_id,
                "via": "regrade",
            },
        )
    await audit_service.log_event(
        db,
        audit_service.STUDENT_NOTIFICATION_CREATED,
        user_id=student_id,
        ip=ip,
        details={
            "notification_id": notification.id,
            "kind": notif_kind,
            "result_id": result_id,
        },
    )

    await db.commit()

    return ReviewRegradeResponse(
        result_id=result_id,
        task_id=task_id,
        old=ReviewRegradePartScore(score=old_score, is_correct=old_is_correct),
        new=ReviewRegradePartScore(score=new_score, is_correct=new_is_correct),
        comment=comment_v,
        checked_at=checked_at,
        notification_id=notification.id,
    )


@router.get(
    "/pending-count",
    response_model=PendingCountResponse,
    status_code=status.HTTP_200_OK,
    summary="Количество pending-заявок преподавателя без захвата (Phase Y-4)",
)
async def get_pending_count_endpoint(
    teacher_id: int = Query(..., description="ID преподавателя"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> PendingCountResponse:
    """Используется TG_LMS поллером — не захватывает заявки, не обновляет БД."""
    if not current_user.is_service and current_user.id != teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    count, oldest = await get_pending_count(db, teacher_id)
    return PendingCountResponse(count=count, oldest_received_at=oldest)


@router.get(
    "/pending",
    response_model=PendingReviewListResponse,
    status_code=status.HTTP_200_OK,
    summary="Очередь ожидающих ручной проверки работ преподавателя (tsk-298 Фаза 2)",
    responses={
        200: {"description": "Список очереди (возможно пустой)"},
        403: {"description": "Чужой teacher_id"},
    },
)
async def list_pending_reviews_endpoint(
    teacher_id: int = Query(..., description="ID преподавателя"),
    course_id: Optional[int] = Query(None, description="Фильтр по курсу"),
    limit: int = Query(50, ge=1, le=200, description="Размер страницы (max 200)"),
    offset: int = Query(0, ge=0, description="Смещение"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> PendingReviewListResponse:
    """Read-only очередь ручной проверки для веб-портала преподавателя (SPW).

    Тот же предикат обязательной очереди, что у `claim-next`, но без захвата.
    ACL-scope — `REVIEW_ACL_SQL` внутри запроса (только работы в course-tree
    преподавателя) + identity-гейт по `teacher_id`. Полный ответ ученика здесь
    не отдаётся (лёгкий item) — он приходит при claim конкретной работы.
    """
    if not current_user.is_service and current_user.id != teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    items, total = await list_pending_reviews(
        db, teacher_id, course_id=course_id, limit=limit, offset=offset
    )
    return PendingReviewListResponse(
        items=[PendingReviewItem(**it) for it in items], total=total
    )


def _render_inbox_content(
    score: int, max_score: int, comment: Optional[str], is_correct: bool
) -> str:
    """Готовый текст inbox-уведомления для ученика.

    Y-6: разное вступление для positive/negative grade.
    """
    if is_correct:
        base = f"Преподаватель принял ответ: {score}/{max_score}"
    else:
        base = (
            f"Преподаватель не принял ответ ({score}/{max_score}). "
            "Задача снова в очереди — вы можете отправить новый ответ."
        )
    if comment:
        # Ограничение длины content — по уроку UX (inbox показывает короткий текст);
        # полный comment доступен через payload.comment.
        clipped = comment if len(comment) <= 200 else comment[:200] + "…"
        return f"{base} {clipped}"
    return base


async def _send_email_and_audit_failure(
    *,
    recipient_email: str,
    task_title: Optional[str],
    score: int,
    max_score: int,
    comment: Optional[str],
    student_id: int,
) -> None:
    """Отправить email best-effort. На failure — открыть новую DB session
    и записать audit_event 'email.failed'. Никогда не raises (fire-and-forget)."""
    try:
        ok = await notification_email_service.send_sa_com_graded(
            recipient_email=recipient_email,
            task_title=task_title,
            score=score,
            max_score=max_score,
            comment=comment,
            settings=_settings,
        )
    except Exception:
        logger.exception("send_sa_com_graded raised unexpectedly")
        ok = False

    if ok:
        return

    # Лучше попытаться записать audit, но без блокировки/raise
    try:
        from app.db.session import async_session_factory
        async with async_session_factory() as db:
            await audit_service.log_event(
                db,
                audit_service.EMAIL_FAILED,
                user_id=student_id,
                details={
                    "kind": "sa_com_graded",
                    "recipient_email_masked": _mask_email_for_audit(recipient_email),
                },
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to write email.failed audit event")


def _mask_email_for_audit(email: str) -> str:
    if "@" not in email:
        return email[:3] + "***"
    local, domain = email.split("@", 1)
    return (local[:3] + "***@" + domain) if len(local) >= 3 else "***@" + domain
