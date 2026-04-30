"""
Teacher reviews API (Learning Engine V1, этап 3.9 + Phase Y-4):
- POST /api/v1/teacher/reviews/claim-next      — взять следующий результат
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
    ReviewClaimItem,
    ReviewClaimNextRequest,
    ReviewClaimNextResponse,
    ReviewGradeRequest,
    ReviewGradeResponse,
    ReviewReleaseRequest,
    ReviewReleaseResponse,
)
from app.services import audit_service, inbox_service, notification_email_service
from app.services.teacher_queue_service import (
    GradeConflictError,
    GradeNotFoundError,
    GradeValidationError,
    claim_next_review,
    get_pending_count,
    grade_review,
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
            is_correct=body.is_correct,
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

    # Inbox запись (внутри той же транзакции — атомарно с UPDATE)
    inbox_content = _render_inbox_content(score, max_score, comment_v)
    notification = await inbox_service.create_for_user(
        db,
        user_id=student_id,
        kind="sa_com_graded",
        title="Преподаватель оценил вашу попытку",
        content=inbox_content,
        payload={
            "task_id": task_id,
            "attempt_id": grade_data.get("attempt_id"),
            "score": score,
            "max_score": max_score,
            "is_correct": is_correct_v,
            "comment": comment_v,
        },
        created_by=body.teacher_id,
    )

    # Audit events
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
        },
    )
    await audit_service.log_event(
        db,
        audit_service.STUDENT_NOTIFICATION_CREATED,
        user_id=student_id,
        ip=ip,
        details={
            "notification_id": notification.id,
            "kind": "sa_com_graded",
            "result_id": result_id,
        },
    )

    await db.commit()

    # Email best-effort после commit. Не передаём db в background — open new session
    # внутри _send_email_after_commit при необходимости (audit email.failed).
    recipient_email = grade_data.get("user_email")
    if recipient_email:
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


def _render_inbox_content(
    score: int, max_score: int, comment: Optional[str]
) -> str:
    """Готовый текст inbox-уведомления для ученика."""
    base = f"Преподаватель оценил вашу попытку: {score}/{max_score}"
    if comment:
        # Ограничение длины content — по уроку UX (inbox показывает короткий текст);
        # полный comment доступен через payload.comment.
        clipped = comment if len(comment) <= 200 else comment[:200] + "…"
        return f"{base}. {clipped}"
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
